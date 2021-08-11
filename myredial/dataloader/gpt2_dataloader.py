from header import *
from .utils import *
from .util_func import *


class GPT2Dataset(Dataset):
    
    def __init__(self, vocab, path, **args):
        self.args = args
        self.vocab = vocab
        self.pad = self.vocab.convert_tokens_to_ids('[PAD]')
        self.sep = self.vocab.convert_tokens_to_ids('[SEP]')
        self.cls = self.vocab.convert_tokens_to_ids('[CLS]')
        self.unk = self.vocab.convert_tokens_to_ids('[UNK]')
        
        if self.args['mode'] == 'test':
            # for test batch generation
            print(f'[!] set the padding side as the left')
            self.vocab.padding_side = 'left'

        suffix = args['tokenizer'].replace('/', '_')
        self.pp_path = f'{os.path.splitext(path)[0]}_gpt2_{suffix}.pt'
        if os.path.exists(self.pp_path):
            self.data = torch.load(self.pp_path)
            print(f'[!] load preprocessed file from {self.pp_path}')
            return None

        if self.args['mode'] == 'train':
            data = read_text_data_line_by_line(path)

            self.data = []
            for text in tqdm(data):
                item = self.vocab.encode(text, add_special_tokens=False)
                ids = [self.cls] + item[:self.args['max_len']-2] + [self.sep]
                self.data.append({
                    'ids': ids,
                    'text': text,
                })
        else:
            data = torch.load(f'{os.path.splitext(path)[0]}.pt')
            self.data = []
            for prefix, pos, neg in tqdm(data):
                # prefix
                item = self.vocab.encode(prefix, add_special_tokens=False)
                ids = [self.cls] + item[(-self.args['max_len']-1):]
                
                item = self.vocab.encode(prefix+pos, add_special_tokens=False)
                pos_ids = [self.cls] + item[:self.args['max_len']-2] + [self.sep]
                
                item = self.vocab.encode(prefix+neg, add_special_tokens=False)
                neg_ids = [self.cls] + item[:self.args['max_len']-2] + [self.sep]

                self.data.append({
                    'ids': ids,
                    'text': prefix,
                    'pos_ids': pos_ids,
                    'pos_text': prefix+pos,
                    'neg_ids': neg_ids,
                    'neg_text': prefix+neg,
                })


    def __len__(self):
        return len(self.data)

    def __getitem__(self, i):
        bundle = self.data[i]
        if self.args['mode'] == 'train':
            ids = torch.LongTensor(bundle['ids'])
            text = bundle['text']
            return ids, text
        else:
            ids = torch.LongTensor(bundle['ids'])
            pos_ids = torch.LongTensor(bundle['pos_ids'])
            neg_ids = torch.LongTensor(bundle['neg_ids'])
            return ids, pos_ids, neg_ids, bundle['text'], bundle['pos_text'], bundle['neg_text']

    def save(self):
        data = torch.save(self.data, self.pp_path)
        print(f'[!] save preprocessed dataset into {self.pp_path}')
        
    def collate(self, batch):
        if self.args['mode'] == 'train':
            ids = [i[0] for i in batch]
            text = [i[1] for i in batch]
            ids = pad_sequence(ids, batch_first=True, padding_value=self.pad)
            mask = generate_mask(ids)
            ids, mask = to_cuda(ids, mask)
            return {
                'ids': ids, 
                'mask': mask, 
                'text': text
            }
        else:
            ids = [i[0] for i in batch]
            pos_ids = [i[1] for i in batch]
            neg_ids = [i[2] for i in batch]
            text = [i[3] for i in batch]
            pos_text = [i[4] for i in batch]
            neg_text = [i[5] for i in batch]

            # pad from the left side, batch first
            max_length = max([len(i) for i in ids])
            n_ids = []
            for i in ids:
                ids_ = torch.cat([torch.LongTensor([self.pad] * (max_length - len(i))), i])
                n_ids.append(ids_)
            ids = torch.stack(n_ids)
            mask = generate_mask(ids)
            
            pos_ids = pad_sequence(pos_ids, batch_first=True, padding_value=self.pad)
            pos_ids_mask = generate_mask(pos_ids)
            neg_ids = pad_sequence(neg_ids, batch_first=True, padding_value=self.pad)
            neg_ids_mask = generate_mask(neg_ids)
            ids, mask, pos_ids, pos_ids_mask, neg_ids, neg_ids_mask = to_cuda(ids, mask, pos_ids, pos_ids_mask, neg_ids, neg_ids_mask)
            return {
                'ids': ids, 
                'mask': mask, 
                'pos_ids': pos_ids, 
                'pos_ids_mask': pos_ids_mask, 
                'neg_ids': neg_ids, 
                'neg_ids_mask': neg_ids_mask, 
                'text': text,
                'pos_text': pos_text,
                'neg_text': neg_text,
            }