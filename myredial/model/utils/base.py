from .header import *

'''
Base Agent
'''

class RetrievalBaseAgent:

    def __init__(self):
        # open the test save scores file handler
        pass

    def show_parameters(self, args):
        print(f'========== Model Parameters ==========')
        for key, value in args.items():
            print(f'{key}: {value}')
        print(f'========== Model Parameters ==========')

    def save_model(self, path):
        try:
            state_dict = self.model.module.state_dict()
        except:
            state_dict = self.model.state_dict()
        torch.save(state_dict, path)
        print(f'[!] save model into {path}')
    
    def train_model(self, train_iter, mode='train'):
        raise NotImplementedError

    def test_model(self, test_iter):
        raise NotImplementedError

    def set_test_interval(self):
        self.args['test_step'] = [int(self.args['total_step']*i) for i in np.arange(0, 1+self.args['test_interval'], self.args['test_interval'])]
        self.test_step_counter = 0

    def test_now(self, test_iter, recoder):
        # test in the training loop
        index = self.test_step_counter
        (r10_1, r10_2, r10_5), avg_mrr, avg_p1, avg_map = self.test_model(test_iter)
        self.model.train()    # reset the train mode
        recoder.add_scalar(f'train-test/R10@1', r10_1, index)
        recoder.add_scalar(f'train-test/R10@2', r10_2, index)
        recoder.add_scalar(f'train-test/R10@5', r10_5, index)
        recoder.add_scalar(f'train-test/MRR', avg_mrr, index)
        recoder.add_scalar(f'train-test/P@1', avg_p1, index)
        recoder.add_scalar(f'train-test/MAP', avg_map, index)
        self.test_step_counter += 1

    def load_checkpoint(self):
        if 'checkpoint' in self.args:
            if self.args['checkpoint']['is_load']:
                path = self.args['checkpoint']['path']
                path = f'{self.args["root_dir"]}/ckpt/{self.args["dataset"]}/{path}'
                self.load_bert_model(path)
                print(f'[!] load checkpoint from {path}')
            else:
                print(f'[!] DONOT load checkpoint')
        else:
            print(f'[!] No checkpoint information found')

    def load_bert_model(self, path):
        raise NotImplementedError

    def set_optimizer_scheduler_ddp(self):
        if self.args['mode'] in ['train']:
            self.optimizer = transformers.AdamW(
                self.model.parameters(), 
                lr=self.args['lr'],
            )
            self.scaler = GradScaler()
            self.scheduler = transformers.get_linear_schedule_with_warmup(
                self.optimizer, 
                num_warmup_steps=self.args['warmup_step'], 
                num_training_steps=self.args['total_step'],
            )
            self.model = nn.parallel.DistributedDataParallel(
                self.model, 
                device_ids=[self.args['local_rank']], 
                output_device=self.args['local_rank'],
                find_unused_parameters=True,
            )
        elif self.args['mode'] in ['inference']:
            self.model = nn.parallel.DistributedDataParallel(
                self.model, 
                device_ids=[self.args['local_rank']], 
                output_device=self.args['local_rank'],
                find_unused_parameters=True,
            )
        else:
            # test doesn't need DDP
            pass

    def load_model(self, path):
        # for test and inference
        state_dict = torch.load(path, map_location=torch.device('cpu'))
        try:
            self.model.module.load_state_dict(state_dict)
        except:
            self.model.load_state_dict(state_dict)
        print(f'[!] load model from {path}')

    def convert_to_text(self, ids):
        '''convert to text and ignore the padding token'''
        tokens = [self.vocab.convert_ids_to_tokens(i) for i in ids.cpu().tolist() if i != self.vocab.pad_token_id]
        text = ''.join(tokens)
        return text

    @torch.no_grad()
    def rerank(self, contexts, candidates):
        raise NotImplementedError

    def _length_limit(self, ids):
        # also return the speaker embeddings
        if len(ids) > self.args['max_len']:
            ids = [ids[0]] + ids[-(self.args['max_len']-1):]
        return ids
    
    def _length_limit_res(self, ids):
        # cut tail
        if len(ids) > self.args['res_max_len']:
            ids = ids[:self.args['res_max_len']-1] + [self.sep]
        return ids

    def totensor(self, texts, ctx=True):
        items = self.vocab.batch_encode_plus(texts)['input_ids']
        if ctx:
            ids = [torch.LongTensor(self._length_limit(i)) for i in items]
        else:
            ids = [torch.LongTensor(self._length_limit_res(i)) for i in items]
        ids = pad_sequence(ids, batch_first=True, padding_value=self.pad)
        mask = self.generate_mask(ids)
        if torch.cuda.is_available():
            ids, mask = ids.cuda(), mask.cuda()
        return ids, mask
        
    def generate_mask(self, ids):
        attn_mask_index = ids.nonzero().tolist()   # [PAD] IS 0
        attn_mask_index_x, attn_mask_index_y = [i[0] for i in attn_mask_index], [i[1] for i in attn_mask_index]
        attn_mask = torch.zeros_like(ids)
        attn_mask[attn_mask_index_x, attn_mask_index_y] = 1
        return attn_mask
