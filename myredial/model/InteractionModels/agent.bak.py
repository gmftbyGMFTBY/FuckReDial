from model.utils import *

class InteractionAgent(RetrievalBaseAgent):

    def __init__(self, vocab, model, args):
        super(InteractionAgent, self).__init__()
        self.args = args
        self.vocab, self.model = vocab, model
        self.pad = self.vocab.convert_tokens_to_ids('[PAD]')
        self.sep = self.vocab.convert_tokens_to_ids('[SEP]')

        if self.args['model'] in ['bert-ft-compare']:
            self.test_model = self.test_model_compare

        if args['mode'] == 'train':
            self.set_test_interval()
            self.load_checkpoint()
        else:
            # open the test save scores file handler
            pretrained_model_name = self.args['pretrained_model'].replace('/', '_')
            path = f'{self.args["root_dir"]}/rest/{self.args["dataset"]}/{self.args["model"]}/scores_log_{pretrained_model_name}.txt'
            self.log_save_file = open(path, 'w')
        if torch.cuda.is_available():
            self.model.cuda()
        if args['mode'] in ['train', 'inference']:
            self.set_optimizer_scheduler_ddp()
        self.criterion = nn.BCEWithLogitsLoss()
        self.show_parameters(self.args)
        
    def load_bert_model(self, path):
        state_dict = torch.load(path, map_location=torch.device('cpu'))
        self.model.load_bert_model(state_dict)
        print(f'[!] load pretrained BERT model from {path}')

    def train_model(self, train_iter, test_iter, recoder=None, idx_=0):
        self.model.train()
        total_loss, batch_num, correct, s = 0, 0, 0, 0
        pbar = tqdm(train_iter)
        correct, s = 0, 0
        for idx, batch in enumerate(pbar):
            self.optimizer.zero_grad()
            with autocast():
                output = self.model(batch)    # [B]
                label = batch['label']
                loss = self.criterion(output, label.to(torch.float))
            self.scaler.scale(loss).backward()
            self.scaler.unscale_(self.optimizer)
            clip_grad_norm_(self.model.parameters(), self.args['grad_clip'])
            self.scaler.step(self.optimizer)
            self.scaler.update()
            self.scheduler.step()

            total_loss += loss.item()
            batch_num += 1
            if batch_num in self.args['test_step']:
                self.test_now(test_iter, recoder)
            
            output = torch.sigmoid(output) > 0.5
            now_correct = torch.sum(output == label).item()
            correct += now_correct
            s += len(label)
            
            recoder.add_scalar(f'train-epoch-{idx_}/Loss', total_loss/batch_num, idx)
            recoder.add_scalar(f'train-epoch-{idx_}/RunLoss', loss.item(), idx)
            recoder.add_scalar(f'train-epoch-{idx_}/Acc', correct/s, idx)
            recoder.add_scalar(f'train-epoch-{idx_}/RunAcc', now_correct/len(label), idx)

            pbar.set_description(f'[!] train loss: {round(loss.item(), 4)}|{round(total_loss/batch_num, 4)}; acc: {round(now_correct/len(label), 4)}|{round(correct/s, 4)}')
        recoder.add_scalar(f'train-whole/Loss', total_loss/batch_num, idx_)
        recoder.add_scalar(f'train-whole/Acc', correct/s, idx_)
        return round(total_loss / batch_num, 4)
    
    @torch.no_grad()
    def test_model(self, test_iter, print_output=False, rerank_agent=None):
        self.model.eval()
        pbar = tqdm(test_iter)
        total_mrr, total_prec_at_one, total_map = 0, 0, 0
        total_examples, total_correct = 0, 0
        k_list = [1, 2, 5, 10]
        for idx, batch in enumerate(pbar):
            label = batch['label']
            scores = torch.sigmoid(self.model(batch)).cpu().tolist()
            
            # print output
            if print_output:
                for ids, score in zip(batch['ids'], scores):
                    text = self.convert_to_text(ids)
                    score = round(score, 4)
                    self.log_save_file.write(f'[Score {score}] {text}\n')
                self.log_save_file.write('\n')
            
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
        return {
            f'R10@{k_list[0]}': round(((total_correct[0]/total_examples)*100), 2),        
            f'R10@{k_list[1]}': round(((total_correct[1]/total_examples)*100), 2),        
            f'R10@{k_list[2]}': round(((total_correct[2]/total_examples)*100), 2),        
            'MRR': round(avg_mrr, 4),
            'P@1': round(avg_prec_at_one, 4),
            'MAP': round(avg_map, 4),
        }
    
    @torch.no_grad()
    def test_model_compare(self, test_iter, print_output=False, rerank_agent=None):
        '''bert-ft-compare only need to test the accuracy'''
        self.model.eval()
        pbar = tqdm(test_iter)
        total_acc_num, total_num = 0, 0
        for batch in pbar:
            label = batch['label']
            scores = torch.sigmoid(self.model(batch))    # [20]
            acc = ((scores > 0.5) == label).to(torch.float).sum().item()
            total_num += len(scores)
            total_acc_num += acc
            scores = scores.tolist()
            
            # print output
            if print_output:
                for ids, score in zip(batch['ids'], scores):
                    text = self.convert_to_text(ids)
                    score = round(score, 4)
                    self.log_save_file.write(f'[Score {score}] {text}\n')
                self.log_save_file.write('\n')
        return {'Acc': round(total_acc_num/total_num, 4)}

    @torch.no_grad()
    def compare_one_turn(self, cids, rids, tickets):
        ids, tids, recoder = [], [], []
        for i, j in tickets:
            rids1, rids2 = rids[i], rids[j]
            ids_ = cids + rids1 + rids2
            tids_ = [0] * len(cids) + [1] * len(rids1) + [2] * len(rids2)
            ids.append(ids_)
            tids.append(tids_)
            recoder.append((i, j))
        ids = [torch.LongTensor(i) for i in ids]
        tids = [torch.LongTensor(i) for i in tids]
        ids = pad_sequence(ids, batch_first=True, padding_value=self.pad)
        tids = pad_sequence(tids, batch_first=True, padding_value=self.pad)
        mask = self.generate_mask(ids)
        if torch.cuda.is_available():
            ids, tids, mask = ids.cuda(), tids.cuda(), mask.cuda()

        # ===== make compare ===== # 
        batch_packup = {
            'ids': ids,
            'tids': tids,
            'mask': mask,
        }
        comp_scores = torch.sigmoid(self.model(batch_packup))    # [B]
        comp_label = (comp_scores > 0.5).tolist()
        return comp_label, recoder

    @torch.no_grad()
    def compare_reorder(self, batch):
        '''
        input: batch = {
            'context': 'text string of the multi-turn conversation context, [SEP] is used for cancatenation',
            'responses': ['candidate1', 'candidate2', ...],
            'scores': [s1, s2, ...],
        }
        output the updated scores for the batch, the order of the responses should not be changed, only the scores are changed.
        '''
        self.model.eval() 
        compare_turn_num = self.args['compare_turn_num']
        max_inner_bsz = self.args['max_inner_bsz']
        context = batch['context']
        scores = batch['scores']
        items = self.vocab.batch_encode_plus([context] + batch['responses'])['input_ids']
        cids = self._length_limit(items[0])
        rids = [self._length_limit_res(i) for i in items[1:]]
        # sort the rids (decrease order)
        order = np.argsort(scores)[::-1].tolist()
        backup_map = {o:i for i, o in enumerate(order)}    # old:new
        rids = [rids[i] for i in order]
        scores = [scores[i] for i in order]

        # tickets to the comparsion function
        tickets = []
        for idx in range(compare_turn_num):
            for i in range(len(rids)-1-idx):
                tickets.append((i, i+1+idx))
        label, recoder = self.compare_one_turn(cids, rids, tickets)
        d = {}
        for l, (i, j) in zip(label, recoder):
            if l:
                continue
            if j in d:
                if i < d[j]:
                    d[j] = i
            else:
                d[j] = i
        d = sorted(list(d.items()), key=lambda x:x[0])
        before_dict = {i:i-1 for i in range(len(rids))}
        for j, i in d:
            # put the j before i (plus the scores)
            s_j, s_i = scores[j], scores[i]
            # get before score
            if before_dict[i] == -1:
                s_i_before = scores[i] + 2.
            else:
                s_i_before = scores[before_dict[i]]
            delta = s_i_before - s_i
            delta_s = random.uniform(0, delta)
            scores[j] = s_i + delta_s    # bigger than s_i but lower than s_i_before
            # change the before dict
            before_dict[j] = before_dict[i]
            before_dict[i] = j
        # backup the scores
        scores = [scores[backup_map[i]] for i in range(len(order))]
        return scores

    def _length_limit(self, ids):
        if len(ids) > self.args['max_len']:
            # cls tokens
            ids = [ids[0]] + ids[-(self.args['max_len']-1):]
        return ids

    def _length_limit_res(self, rids):
        if len(rids) > self.args['res_max_len']:
            # ignore the cls token
            rids = rids[1:self.args['res_max_len']] + [self.sep]
        else:
            rids = rids[1:]
        return rids