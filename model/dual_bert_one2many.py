from .header import *
from .base import *
from .utils import *


class BertEmbedding(nn.Module):
    
    def __init__(self, model='bert-base-chinese', p=0.2):
        super(BertEmbedding, self).__init__()
        self.model = BertModel.from_pretrained(model)
        if model in ['bert-base-uncased']:
            # english corpus has three special tokens: __number__, __url__, __path__
            self.model.resize_token_embeddings(self.model.config.vocab_size + 3)

    def forward(self, ids, attn_mask, m=0):
        embd = self.model(ids, attention_mask=attn_mask)[0]    # [B, S, 768]
        embd = embd[:, 0, :]    # [B, E]
        return embd
    
    def load_bert_model(self, state_dict):
        new_state_dict = OrderedDict()
        for k, v in state_dict.items():
            if k.startswith('_bert_model.cls.'):
                continue
            name = k.replace('_bert_model.bert.', '')
            new_state_dict[name] = v
        self.model.load_state_dict(new_state_dict)
    

class BERTDualOne2ManyEncoder(nn.Module):

    def __init__(self, model='bert-base-chinese', head=5, p=0.1):
        super(BERTDualOne2ManyEncoder, self).__init__()
        self.ctx_encoder = BertEmbedding(model=model, p=p)
        self.can_encoder = BertEmbedding(model=model, p=p)
        self.head_proj = nn.Parameter(torch.randn(head, 768))
        self.head_num = head

    def _encode(self, cid, rids, cid_mask, rids_mask):
        cid_rep = self.ctx_encoder(cid, cid_mask)    # [B, S, E]
        weights = torch.matmul(cid_rep, self.head_proj.t()).permute(0, 2, 1)    # [B, H, S]
        weights /= np.sqrt(768)
        cid_mask_ = torch.where(cid_mask != 0, torch.zeros_like(cid_mask), torch.ones_like(cid_mask))
        cid_mask_ = cid_mask_ * -1e3
        cid_mask_ = cid_mask.unsqueeze(1).repeat(1, self.head_num, 1)    # [B, H, S]
        weights += cid_mask_
        weights = F.softmax(weights, dim=-1)    # [B, H, S]
        cid_reps = torch.bmm(weights, cid_rep)    # [B, H, E]

        rid_reps = []
        for rid, rid_mask in zip(rids, rids_mask):
            rid_rep = self.can_encoder(rid, rid_mask)
            rid_rep = rid_rep[:, 0, :]    # [B, E]
            rid_reps.append(rid_rep)
        return cid_reps, rid_reps

    @torch.no_grad()
    def _encode_(self, cid, rid, cid_mask, rid_mask):
        cid_rep = self.ctx_encoder(cid, cid_mask)
        weights = torch.matmul(cid_rep, self.head_proj.t()).permute(0, 2, 1)    # [B, H, S]
        weights /= np.sqrt(768)
        cid_mask_ = torch.where(cid_mask != 0, torch.zeros_like(cid_mask), torch.ones_like(cid_mask))
        cid_mask_ = cid_mask_ * -1e3
        cid_mask_ = cid_mask.unsqueeze(1).repeat(1, self.head_num, 1)    # [B, H, S]
        weights += cid_mask_
        weights = F.softmax(weights, dim=-1)    # [B, H, S]
        cid_reps = torch.bmm(weights, cid_rep)    # [B, H, E]

        rid_rep = self.can_encoder(rid, rid_mask)
        return cid_reps, rid_rep

    @torch.no_grad()
    def predict(self, cid, rid, rid_mask):
        batch_size = rid.shape[0]
        cid = cid.unsqueeze(0)
        cid_mask = torch.ones_like(cid).cuda()
        cid_reps, rid_rep = self._encode_(cid, rid, cid_mask, rid_mask)
        # cid_rep/rid_rep: [1, H, 768], [B, 768]
        cid_rep = cid_rep.squeeze(0)    # [H, 768]
        dot_product = torch.matmul(cid_rep, rid_rep.t()).max(dim=0)    # [H, B] -> [B]
        return dot_product
    
    def forward(self, cid, rids, cid_mask, rids_mask):
        batch_size = cid.shape[0]
        assert batch_size > 1, f'[!] batch size must bigger than 1, cause other elements in the batch will be seen as the negative samples'
        cid_reps, rid_reps = self._encode(cid, rids, cid_mask, rids_mask)

        # ========== K matrixes =========== #
        # cid_rep/rid_rep: [B, 768]
        mask = torch.eye(batch_size).cuda().half()    # [B, B]
        acc, loss, additional_matrix, neg_additional_matrix = 0, 0, [], []
        counter = 0
        for rid_rep in rid_reps:
            dot_product = torch.matmul(cid_rep, rid_rep.t())  # [B, B]
            loss_ = F.log_softmax(dot_product, dim=-1) * mask
            loss_ = (-loss_.sum(dim=1)).mean()
            loss += loss_

            if counter == 0:
                acc_num = (F.softmax(dot_product, dim=-1).max(dim=-1)[1] == torch.LongTensor(torch.arange(batch_size)).cuda()).sum().item()
                acc = acc_num / batch_size
            additional_matrix.append(dot_product[range(batch_size), range(batch_size)])
            counter += 1

        loss /= self.head_num
        additional_matrix = torch.stack(additional_matrix).transpose(0, 1)
        mask_ = torch.zeros_like(additional_matrix).cuda()
        mask_[:, 0] = 1
        additional_loss = F.log_softmax(additional_matrix, dim=-1) * mask_
        additional_loss = (-additional_loss.sum(dim=1)).mean()
        loss += additional_loss
        return loss, acc
    
    def forward_(self, cid, rids, cid_mask, rids_mask):
        batch_size = cid.shape[0]
        assert batch_size > 1, f'[!] batch size must bigger than 1, cause other elements in the batch will be seen as the negative samples'
        cid_reps, rid_reps = self._encode(cid, rids, cid_mask, rids_mask)

        # ========== K matrixes =========== #
        # cid_rep/rid_rep: [B, 768]
        # use half for supporting the apex
        mask = torch.eye(batch_size).cuda().half()    # [B, B]
        mask = torch.cat([mask, torch.zeros(batch_size, batch_size*(len(cid_reps)-1)).half().cuda()], dim=-1)    # [B, B*K]
        # mask = torch.eye(batch_size).cuda()    # [B, B]
        # calculate accuracy
        acc, loss, additional_matrix = 0, 0, []
        counter = 0
        dot_products = []
        for cid_rep, rid_rep in zip(cid_reps, rid_reps):
            dot_product = torch.matmul(cid_rep, rid_rep.t())  # [B, B]
            dot_products.append(dot_product)
        dot_products = torch.cat(dot_products, dim=-1)    # [B, B*K]
        # calculate the loss
        loss_ = F.log_softmax(dot_products, dim=-1) * mask
        loss = (-loss_.sum(dim=1)).mean()
        acc_num = (F.softmax(dot_products, dim=-1).max(dim=-1)[1] == torch.LongTensor(torch.arange(batch_size)).cuda()).sum().item()
        acc = acc_num / batch_size
        return loss, acc
    
    
class BERTDualOne2ManyEncoderAgent(RetrievalBaseAgent):
    
    def __init__(self, multi_gpu, total_step, warmup_step, run_mode='train', local_rank=0, dataset_name='ecommerce', pretrained_model='bert-base-chinese', pretrained_model_path=None, head=10):
        super(BERTDualOne2ManyEncoderAgent, self).__init__()
        try:
            self.gpu_ids = list(range(len(multi_gpu.split(',')))) 
        except:
            raise Exception(f'[!] multi gpu ids are needed, but got: {multi_gpu}')
        self.args = {
            'lr': 5e-5,
            'grad_clip': 1.0,
            'multi_gpu': self.gpu_ids,
            'model': pretrained_model,
            'amp_level': 'O2',
            'local_rank': local_rank,
            'warmup_steps': warmup_step,
            'total_step': total_step,
            'max_len': 256,
            'dataset': dataset_name,
            'pretrained_model_path': pretrained_model_path,
            'oom_times': 10,
            'head_num': head,
            'dropout': 0.2,
            'margin': 0.1,
            'pseudo_ratio': 2,
            'm': 5,
        }
        self.vocab = BertTokenizer.from_pretrained(self.args['model'])
        self.model = BERTDualOne2ManyEncoder(model=self.args['model'], head=self.args['head_num'], p=self.args['dropout'])
        if pretrained_model_path:
            self.load_bert_model(pretrained_model_path)
        if torch.cuda.is_available():
            self.model.cuda()
        self.optimizer = transformers.AdamW(
            self.model.parameters(), 
            lr=self.args['lr'],
        )
        if run_mode == 'train':
            self.model, self.optimizer = amp.initialize(
                self.model, 
                self.optimizer,
                opt_level=self.args['amp_level'],
            )
            self.scheduler = transformers.get_linear_schedule_with_warmup(
                self.optimizer, 
                num_warmup_steps=warmup_step, 
                num_training_steps=total_step,
            )
            self.model = nn.parallel.DistributedDataParallel(
                self.model, device_ids=[local_rank], output_device=local_rank,
                find_unused_parameters=True,
            )
        elif run_mode == 'inference':
            self.model = nn.parallel.DistributedDataParallel(
                self.model, device_ids=[local_rank], output_device=local_rank,
                find_unused_parameters=True,
            )
        self.show_parameters(self.args)
        
    def load_bert_model(self, path):
        state_dict = torch.load(path, map_location=torch.device('cpu'))
        self.model.ctx_encoder.load_bert_model(state_dict)
        self.model.can_encoder.load_bert_model(state_dict)
        print(f'[!] load pretrained BERT model from {path}')
        
    def train_model(self, train_iter, mode='train', recoder=None, idx_=0):
        '''ADD OOM ASSERTION'''
        self.model.train()
        total_loss, total_acc, batch_num = 0, 0, 0
        pbar = tqdm(train_iter)
        correct, s, oom_t = 0, 0, 0
        for idx, batch in enumerate(pbar):
            self.optimizer.zero_grad()
            cid, rid, cid_mask, rid_mask = batch
            loss, acc = self.model(cid, rid, cid_mask, rid_mask)
            with amp.scale_loss(loss, self.optimizer) as scaled_loss:
                scaled_loss.backward()
            clip_grad_norm_(amp.master_params(self.optimizer), self.args['grad_clip'])
            self.optimizer.step()
            self.scheduler.step()

            total_loss += loss.item()
            total_acc += acc
            batch_num += 1
            
            recoder.add_scalar(f'train-epoch-{idx_}/Loss', total_loss/batch_num, idx)
            recoder.add_scalar(f'train-epoch-{idx_}/RunLoss', loss.item(), idx)
            recoder.add_scalar(f'train-epoch-{idx_}/Acc', total_acc/batch_num, idx)
            recoder.add_scalar(f'train-epoch-{idx_}/RunAcc', acc, idx)
            
            pbar.set_description(f'[!] loss: {round(loss.item(), 4)}|{round(total_loss/batch_num, 4)}; acc: {round(acc, 4)}|{round(total_acc/batch_num, 4)}')
        recoder.add_scalar(f'train-whole/Loss', total_loss/batch_num, idx_)
        recoder.add_scalar(f'train-whole/Acc', total_acc/batch_num, idx_)
        return round(total_loss / batch_num, 4)
        
    @torch.no_grad()
    def test_model(self):
        self.model.eval()
        pbar = tqdm(self.test_iter)
        total_mrr, total_prec_at_one, total_map = 0, 0, 0
        total_examples, total_correct = 0, 0
        k_list = [1, 2, 5, 10]
        for idx, batch in enumerate(pbar):                
            cid, rids, rids_mask, label = batch
            batch_size = len(rids)
            assert batch_size == 10, f'[!] {batch_size} is not equal to 10'
            scores = self.model.module.predict(cid, rids, rids_mask).cpu().tolist()    # [B]
            
            rank_by_pred, pos_index, stack_scores = \
          calculate_candidates_ranking(
                np.array(scores), 
                np.array(label.cpu().tolist()),
                10)
            num_correct = logits_recall_at_k(pos_index, k_list)
            if self.args['dataset'] in ["douban"]:
                total_prec_at_one += precision_at_one(rank_by_pred)
                total_map += mean_average_precision(pos_index)
                for pred in rank_by_pred:
                    if sum(pred) == 0:
                        total_examples -= 1
            total_mrr += logits_mrr(pos_index)
            total_correct = np.add(total_correct, num_correct)
            total_examples += math.ceil(label.size()[0] / 10)
        avg_mrr = float(total_mrr / total_examples)
        avg_prec_at_one = float(total_prec_at_one / total_examples)
        avg_map = float(total_map / total_examples)
        
        for i in range(len(k_list)):
            print(f"R10@{k_list[i]}: {round(((total_correct[i] / total_examples) * 100), 2)}")
        print(f"MRR: {round(avg_mrr, 4)}")
        print(f"P@1: {round(avg_prec_at_one, 4)}")
        print(f"MAP: {round(avg_map, 4)}")
        return (total_correct[0]/total_examples, total_correct[1]/total_examples, total_correct[2]/total_examples), avg_mrr, avg_prec_at_one, avg_map

    @torch.no_grad()
    def inference(self, inf_iter, test_iter):
        self.model.eval()
        pbar = tqdm(inf_iter)
        matrix, corpus, queries, q_text, q_order, q_text_r = [], [], [], [], [], []
        for batch in pbar:
            ids, mask, text = batch
            vec = self.model.module.get_cand(ids, mask).cpu()    # [B, H]
            matrix.append(vec)
            corpus.extend(text)
        matrix = torch.cat(matrix, dim=0).numpy()    # [Size, H]
        assert len(matrix) == len(corpus)

        # context response
        pbar = tqdm(test_iter)
        for batch in pbar:
            ids, ids_mask, ctx_text, res_text, order = batch
            vec = self.model.module.get_ctx(ids, ids_mask).cpu()
            queries.append(vec)
            q_text.extend(ctx_text)
            q_text_r.extend(res_text)
            q_order.extend(order)
        queries = torch.cat(queries, dim=0).numpy()
        torch.save(
            (queries, q_text, q_text_r, q_order, matrix, corpus), 
            f'data/{self.args["dataset"]}/inference_{self.args["local_rank"]}.pt'
        )
