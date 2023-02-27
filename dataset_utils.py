import os, json, random
import pandas as pd
from tqdm import tqdm
from torch.utils.data import Dataset, TensorDataset, DataLoader
from pdb import set_trace

def load_and_filter_raw_data(dataset_dir, descriptions_file, extracted_texts_file):
    '''
    Load the saved data in its original format.
    This function only keeps the problems that have BOTH an image and a lecture.
    '''
    # Load the original data
    problems = json.load(open(os.path.join(dataset_dir, 'problems.json')))
    pid_splits = json.load(open(os.path.join(dataset_dir, 'pid_splits.json')))
    descriptions = pd.read_csv(descriptions_file)
    extracted_texts = json.load(open(extracted_texts_file))
    
    # only keep problems that have an image and a hint
    problems_w_img = {k: v for k, v in problems.items() if v['image'] is not None and len(v['hint']) > 0}
    problems = problems_w_img

    # only keep pids in each of the train, val, and test splits that also appear in the problems dict
    pid_splits['train'] = [pid for pid in pid_splits['train'] if pid in problems]
    pid_splits['val'] = [pid for pid in pid_splits['val'] if pid in problems]
    pid_splits['test'] = [pid for pid in pid_splits['test'] if pid in problems]
    
    # get the remaining pids
    pids = list(problems.keys())
    
    # only keep pids that have a description and extracted texts
    descriptions = descriptions[descriptions['pid'].isin([int(pid) for pid in pids])]
    extracted_texts['train'] = {k: v for k, v in extracted_texts['train'].items() if k in pids}
    extracted_texts['val'] = {k: v for k, v in extracted_texts['val'].items() if k in pids}
    extracted_texts['test'] = {k: v for k, v in extracted_texts['test'].items() if k in pids}
    
    return problems, pid_splits, descriptions, extracted_texts


def format_input(desc, ext_text, hint, lecture, subject, topic, category, input_format_opt):
    # Combine textual context, image caption, and extracted text using a template
    template = 'Generate a question based on the following information.\n'
    
    # option 1: combine text and image. 
    # input is textual context (hint) + image caption (desc) + extracted text (ext_text)
    if input_format_opt == 1:
        template += f'Context: {hint.strip()}\n'
        template += f'Image: {desc}.'
        # if there is further extracted text
        if ext_text is not None and len(ext_text) > 0:
            ext_text = ', '.join([t.strip() for t in ext_text])
            template += f'\nTexts in image: {ext_text.strip()}.'
    
    # option 2: text only
    elif input_format_opt == 2:
        template += hint.strip()
    
    # option 3: image only
    elif input_format_opt == 3:
        template += f'{desc}.'
        # if there is further extracted text
        if ext_text is not None and len(ext_text) > 0:
            ext_text = ', '.join([t.strip() for t in ext_text])
            template += f'\nTexts in image: {ext_text.strip()}.'
            
    else:
        raise Exception('input_format_opt not supported or wrong input_format_opt value: {}'.format(input_format_opt))
            
    template += f'\nSubject: {subject}. Topic: {topic}. Category: {category}.'
    
    return template.strip()


def format_output(question, choices, target_format_opt):
    # use question only
    if target_format_opt == 1:
        return question
    
    # use both question and choices
    elif target_format_opt == 2:
        choices = '\n'.join([f'({idx+1}) ' + choices[idx].strip() for idx in range(len(choices))]).strip()
        return f'Question:\n{question}\n\nChoices:\n{choices}'
        
def process_desc(desc, desc_sel_mode=1):
    '''
    description processing logic
    '''
    if desc_sel_mode == 1:
        # randomly choose one description
        desc = random.choice(desc['generated_descriptions'].tolist())
    elif desc_sel_mode == 2:
        # rerank desc by perplexity, ascending order, then choose the first one
        desc = desc.sort_values(by=['ppls'])['generated_descriptions'].tolist()[0]
    else:
        raise Exception('desc_sel_mode not supported or wrong desc_sel_mode value: {}'.format(desc_sel_mode))
    return desc

def format_io_data(problems, pid_splits, descriptions, extracted_texts, split='train', 
                   desc_sel_mode=1, input_format_opt=1, target_format_opt=1):
    '''
    Format the data in the format that will be used for training.
    Returns:
        inputs: list of strings
        targets: list of strings
    '''
    pid_split = pid_splits[split]
    pids = []
    inputs = []
    targets = []
    for pid in tqdm(pid_split, desc='Formatting data'):
        # process image context (description, extracted text)
        if input_format_opt == 1 or input_format_opt == 3:
            descriptions_pid = descriptions[descriptions['pid'] == int(pid)] # keep both desc and ppl
            extracted_text = extracted_texts[split][pid] # either [] or [text1, text2, ...]
            description = process_desc(descriptions_pid, desc_sel_mode)
        else:
            description = None
            extracted_text = None
        # process hint (textual context)
        hint = problems[pid]['hint'] # either '' or 'hint text'
        # get other info
        lecture = problems[pid]['lecture']
        subject = problems[pid]['subject']
        topic = problems[pid]['topic']
        category = problems[pid]['category']
        # format input and output
        inp = format_input(description, extracted_text, hint, lecture, 
                           subject, topic, category, input_format_opt)
        out = format_output(problems[pid]['question'], 
                            problems[pid]['choices'], target_format_opt)
        inputs.append(inp)
        targets.append(out)
        pids.append(pid)
    return inputs, targets, pids


# Tokenization
def get_transformer_encoding(tokenizer, inputs, targets):
    # tokenizer = T5Tokenizer.from_pretrained(model_name)
    max_source_length, max_target_length = 512, 128
    inp_encoding = tokenizer(inputs, padding='longest', 
                        max_length=max_source_length,
                        truncation=True,
                        return_tensors="pt"
                    )
    input_ids, attention_mask = inp_encoding.input_ids, inp_encoding.attention_mask

    target_encoding = tokenizer(targets, padding='longest', 
                        max_length=max_target_length,
                        truncation=True,
                        return_tensors="pt"
                    )
    labels = target_encoding.input_ids
    # 0 loss for pad tokens
    labels[labels == tokenizer.pad_token_id] = -100
    return input_ids, attention_mask, labels

# Pytorch Dataset
class QGDataset(Dataset):
    def __init__(self, input_ids, attn_masks, labels):
        self.input_ids = input_ids
        self.attn_masks = attn_masks
        self.labels = labels
        
    def __getitem__(self, index):
        x = self.input_ids[index]
        y = self.attn_masks[index]
        z = self.labels[index]
        
        return {'input_ids': x, 'attention_mask': y, 'labels':z}
    
    def __len__(self):
        return len(self.input_ids)

# Dataset
def get_dataloader(batch_size, dataset, datatype='train'):
    if datatype == 'train':
        return DataLoader(dataset=dataset, shuffle=True, batch_size = batch_size)
    else:
        return DataLoader(dataset=dataset, batch_size = batch_size)
    

# # Tests
# dataset_dir = '/data/zw16/dataset_ScienceQA'
# descriptions_file = 'generated_descriptions/blip2-flan-t5-xxl_halfprecTrue_prompt0_gmodeC_pa0.6_topk4_temp1_topp0.95_spTrue_nsp20_minl30_maxl100_seed42.csv'
# extracted_texts_file = 'extracted_texts_img/extracted_texts.json'
# problems, pid_splits, descriptions, extracted_texts = load_and_filter_raw_data(
#     dataset_dir, descriptions_file, extracted_texts_file)

# inputs, targets, pids = format_io_data(problems, pid_splits, descriptions, extracted_texts, split='train', 
#                    desc_sel_mode=2, input_format_opt=1, target_format_opt=2)
# set_trace()