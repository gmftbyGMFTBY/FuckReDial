from .header import *
from .base import *
from .utils import *


class BertEmbedding(nn.Module):
    
    def __init__(self, model='bert-base-chinese'):
        super(BertEmbedding, self).__init__()
        self.model = BertModel.from_pretrained(model)
        if model in ['bert-base-uncased']:
            # english corpus has three special tokens: __number__, __url__, __path__
            self.model.resize_token_embeddings(self.model.config.vocab_size + 3)

    def forward(self, ids, attn_mask, speaker_type_ids=None):
        embds = self.model(ids, attention_mask=attn_mask)[0]
        embds = embds[:, 0, :]     # [CLS]
        return embds
    
    def load_bert_model(self, state_dict):
        new_state_dict = OrderedDict()
        for k, v in state_dict.items():
            if k.startswith('_bert_model.cls.'):
                continue
            name = k.replace('_bert_model.bert.', '')
            new_state_dict[name] = v
        self.model.load_state_dict(new_state_dict)
    

class BERTDualKWEncoder(nn.Module):

    '''dual bert and dual latent interaction: one-to-many mechanism'''
    
    def __init__(self, vocab_size, p=0.1, model='bert-base-chinese', alpha=0.3, beta=0.2, topk=10):
        super(BERTDualKWEncoder, self).__init__()
        self.ctx_encoder = BertEmbedding(model=model)
        self.can_encoder = BertEmbedding(model=model)
        self.kw_predictor = nn.Sequential(
            nn.Linear(768, 768*2),
            nn.ReLU(),
            nn.Dropout(p=p),
            nn.Linear(768*2, vocab_size),
            nn.Sigmoid(),
        )
        self.criterion = nn.BCELoss()
        self.kw_embedding = nn.Embedding(vocab_size, 768)
        # set the pad token word embedding to all he zeros
        ipdb.set_trace()
        self.kw_embedding.data[0].copy_(torch.zeros(768))
        self.alpha = alpha
        self.beta = beta
        self.topk = topk

    def _encode(self, cid, rid, cid_mask, rid_mask):
        cid_rep = self.ctx_encoder(cid, cid_mask)
        rid_rep = self.can_encoder(rid, rid_mask)
        kw_rep = self.kw_predictor(cid_rep)
        return cid_rep, rid_rep, kw_rep

    @torch.no_grad()
    def get_cand(self, ids, attn_mask):
        rid_rep = self.can_encoder(ids, attn_mask)
        return rid_rep

    @torch.no_grad()
    def get_ctx(self, ids, attn_mask):
        cid_rep = self.ctx_encoder(ids, attn_mask)
        return cid_rep

    @torch.no_grad()
    def predict(self, cid, rid, rid_mask, kw_ids, kw_rids):
        batch_size = rid.shape[0]
        cid_rep, rid_rep, kw_rep = self._encode(cid.unsqueeze(0), rid, None, rid_mask)
        # fusion
        kw_rids_rep = self.kw_embedding(kw_rids)    # [B, S, E]
        kw_cids_rep = self.kw_embedding(kw_ids.unsqueeze(0))    # [1, S, E]
        kw_rids_rep = kw_rids_rep.mean(dim=1)
        kw_cids_rep = kw_cids_rep.mean(dim=1)
        # during inference, the rids keywords is unknown, use tok
        pred_kw_rids = torch.topk(kw_rep, self.topk)[0]    # [1, K]
        pred_kw_rids_rep = self.kw_embedding(pred_kw_rids).mean(dim=1)    # [1, K, E] -> [1, E]
        cid_rep += self.alpha * kw_cids_rep + self.beta * pred_kw_rids_rep
        rid_rep += self.alpha * kw_rids_rep

        dot_product = torch.matmul(cid_rep, rid_rep.t()).squeeze(0)
        return dot_product
    
    def forward(self, cid, rid, cid_mask, rid_mask, kw_ids, kw_rids, kw_pred_label):
        batch_size = cid.shape[0]
        cid_rep, rid_rep, kw_rep = self._encode(cid, rid, cid_mask, rid_mask)
        # fusion the keyword embedding
        kw_rids_rep = self.kw_embedding(kw_rids)    # [B, S, E]
        kw_cids_rep = self.kw_embedding(kw_cids)    # [B, S, E]
        kw_rids_rep = kw_rids_rep.mean(dim=1)
        kw_cids_rep = kw_cids_rep.mean(dim=1)
        # TODO: add the teacher force ratio to fill the gap between the training and test procedure
        cid_rep += self.alpha * kw_cids_rep + self.beta * kw_rids_rep
        rid_rep += self.alpha * kw_rids_rep


        dot_product = torch.matmul(cid_rep, rid_rep.t())
        mask = torch.zeros_like(dot_product).cuda()
        mask[range(batch_size), range(batch_size)] = 1.
        # loss
        loss_ = F.log_softmax(dot_product, dim=-1) * mask
        loss = (-loss_.sum(dim=1)).mean()
        # kw loss
        kw_pred_label = kw_pred_label.to(torch.half)
        loss += self.criterion(kw_rep, kw_pred_label)
        # acc
        acc_num = (F.softmax(dot_product, dim=-1).max(dim=-1)[1] == torch.LongTensor(torch.arange(batch_size)).cuda()).sum().item()
        acc = acc_num / batch_size
        return loss, acc
    
    
class BERTDualKWEncoderAgent(RetrievalBaseAgent):
    
    def __init__(self, multi_gpu, total_step, warmup_step, run_mode='train', local_rank=0, dataset_name='ecommerce', pretrained_model='bert-base-chinese', pretrained_model_path=None):
        super(BERTDualKWEncoderAgent, self).__init__()
        try:
            self.gpu_ids = list(range(len(multi_gpu.split(',')))) 
        except:
            raise Exception(f'[!] multi gpu ids are needed, but got: {multi_gpu}')
        self.args = {
            'lr': 5e-5,
            'grad_clip': 1.0,
            'multi_gpu': self.gpu_ids,
            'model': pretrained_model,
            'local_rank': local_rank,
            'warmup_steps': warmup_step,
            'total_step': total_step,
            'dataset': dataset_name,
            'pretrained_model_path': pretrained_model_path,
            'dropout': 0.1,
            'amp_level': 'O2',
            'test_interval': 0.05,
            'vocab_size': 50001,    # add the [PAD] token
            'alpha': 0.3,
            'beta': 0.2,
            'topk': 10,
        }
        self.args['test_step'] = [int(total_step*i) for i in np.arange(0, 1+self.args['test_interval'], self.args['test_interval'])]
        self.test_step_counter = 0

        self.vocab = BertTokenizer.from_pretrained(self.args['model'])
        self.model = BERTDualKWEncoder(
            self.args['vocab_size'],
            model=self.args['model'], 
            p=self.args['dropout'],
            alpha=self.args['alpha'],
            beta=self.args['beta'],
            topk=self.args['topk'],
        )
        if pretrained_model_path:
            self.load_bert_model(pretrained_model_path)
        if torch.cuda.is_available():
            self.model.cuda()
        self.optimizer = transformers.AdamW(
            self.model.parameters(), 
            lr=self.args['lr'],
        )
        if run_mode in ['train', 'train-post', 'train-dual-post']:
            self.model, self.optimizer = amp.initialize(
                self.model,
                self.optimizer,
                opt_level=self.args['amp_level']
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
        elif run_mode in ['inference', 'inference_qa']:
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
        self.model.train()
        total_loss, total_acc, batch_num = 0, 0, 0
        total_tloss, total_bloss = 0, 0
        pbar = tqdm(train_iter)
        correct, s, oom_t = 0, 0, 0
        for idx, batch in enumerate(pbar):
            self.optimizer.zero_grad()
            cid, rid, cid_mask, rid_mask, kw_ids, kw_rids, kw_pred_label = batch
            loss, acc = self.model(cid, rid, cid_mask, rid_mask, kw_ids, kw_rids, kw_pred_label)
            
            with amp.scale_loss(loss, self.optimizer) as scaled_loss:
                scaled_loss.backward()
            clip_grad_norm_(amp.master_params(self.optimizer), self.args['grad_clip'])
            # zero the pad token gradient
            ipdb.set_trace()
            self.model.module.kw_embedding.weight.grad[0] = 0.

            self.optimizer.step()
            self.scheduler.step()

            total_loss += loss.item()
            total_acc += acc
            batch_num += 1

            if batch_num in self.args['test_step']:
                # test in the training loop
                index = self.test_step_counter
                (r10_1, r10_2, r10_5), avg_mrr, avg_p1, avg_map = self.test_model()
                self.model.train()    # reset the train mode
                recoder.add_scalar(f'train-test/R10@1', r10_1, index)
                recoder.add_scalar(f'train-test/R10@2', r10_2, index)
                recoder.add_scalar(f'train-test/R10@5', r10_5, index)
                recoder.add_scalar(f'train-test/MRR', avg_mrr, index)
                recoder.add_scalar(f'train-test/P@1', avg_p1, index)
                recoder.add_scalar(f'train-test/MAP', avg_map, index)
                self.test_step_counter += 1
            
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
            cid, rids, rids_mask, label, kw_ids, kw_rids, kw_pred_label = batch
            batch_size = len(rids)
            assert batch_size == 10, f'[!] {batch_size} is not equal to 10'
            scores = self.model.module.predict(cid, rids, rids_mask, kw_ids, kw_rids).cpu().tolist()    # [B]

            rank_by_pred, pos_index, stack_scores = \
          calculate_candidates_ranking(
                np.array(scores), 
                np.array(label.cpu().tolist()),
                10)
            num_correct = logits_recall_at_k(pos_index, k_list)
            if self.args['dataset'] in ["douban"]:
                # if sum(label).item() >= 2:
                #     ipdb.set_trace()
                total_prec_at_one += precision_at_one(rank_by_pred)
                total_map += mean_average_precision(pos_index)
                for pred in rank_by_pred:
                    if sum(pred) == 0:
                        total_examples -= 1
            total_mrr += logits_mrr(pos_index)
            total_correct = np.add(total_correct, num_correct)
            total_examples += 1
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
    def inference_qa(self, inf_iter):
        self.model.eval()
        pbar = tqdm(inf_iter)
        q, a, o = [], [], []
        for batch in pbar:
            cid, cid_mask, rid, rid_mask, order = batch
            ctx = self.model.module.get_ctx(cid, cid_mask).cpu()
            res = self.model.module.get_cand(rid, rid_mask).cpu()
            q.append(ctx)
            a.append(res)
            o.extend(order)
        q = torch.cat(q, dim=0).numpy()
        a = torch.cat(a, dim=0).numpy()
        torch.save((q, a, o), f'data/{self.args["dataset"]}/inference_qa_{self.args["local_rank"]}.pt')

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
