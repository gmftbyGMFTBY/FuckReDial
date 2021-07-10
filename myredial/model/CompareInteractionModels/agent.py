from model.utils import *
from dataloader.util_func import *

class CompareInteractionAgent(RetrievalBaseAgent):

    def __init__(self, vocab, model, args):
        super(CompareInteractionAgent, self).__init__()
        self.args = args
        self.vocab, self.model = vocab, model
        self.vocab.add_tokens(['[EOS]'])
        self.pad = self.vocab.convert_tokens_to_ids('[PAD]')
        self.sep = self.vocab.convert_tokens_to_ids('[SEP]')
        self.eos = self.vocab.convert_tokens_to_ids('[EOS]')
        self.cls = self.vocab.convert_tokens_to_ids('[CLS]')

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
            # gradient accumulation
            loss, output, label = self.model(batch, scaler=self.scaler, optimizer=self.optimizer)
            self.scaler.unscale_(self.optimizer)
            clip_grad_norm_(self.model.parameters(), self.args['grad_clip'])
            self.scaler.step(self.optimizer)
            self.scaler.update()
            self.scheduler.step()

            if self.args['num_labels'] == 1:
                output = torch.sigmoid(output) > 0.5
                now_correct = torch.sum(output == label).item()
            else:
                output = output.max(dim=-1)[1]
                now_correct = (output.cpu() == label.cpu()).sum().item()
            correct += now_correct
            s += len(label)
            total_loss += loss.item()
            batch_num += 1
            if batch_num in self.args['test_step']:
                self.test_now(test_iter, recoder)
            
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
        for batch in pbar:
            label = np.array(batch['label'])
            packup = {
                'context': batch['context'],
                'responses': batch['responses'],
            }
            scores = self.fully_compare(packup)
            
            # print output
            if print_output:
                c = batch['context']
                self.log_save_file.write(f'[Context] {c}\n')
                for r, score in zip(batch['responses'], scores):
                    score = round(score, 4)
                    self.log_save_file.write(f'[Score {score}] {r}\n')
                self.log_save_file.write('\n')

            rank_by_pred, pos_index, stack_scores = \
          calculate_candidates_ranking(
                np.array(scores), 
                np.array(label.tolist()),
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
            total_examples += math.ceil(label.size / 10)
        avg_mrr = float(total_mrr / total_examples)
        avg_prec_at_one = float(total_prec_at_one / total_examples)
        avg_map = float(total_map / total_examples)
        return {
            f'R10@{k_list[0]}': round(((total_correct[0]/total_examples)*100), 2),        
            f'R10@{k_list[1]}': round(((total_correct[1]/total_examples)*100), 2),        
            f'R10@{k_list[2]}': round(((total_correct[2]/total_examples)*100), 2),        
            'MRR': round(100*avg_mrr, 2),
            'P@1': round(100*avg_prec_at_one, 2),
            'MAP': round(100*avg_map, 2),
        }

    @torch.no_grad()
    def compare_one_turn(self, cids, rids, tickets, margin=0.0, fully=False, fast=False):
        '''Each item pair in the tickets (i, j), the i has the bigger scores than j'''
        ids, tids, recoder = [], [], []
        for i, j in tickets:
            cids_, rids1, rids2 = deepcopy(cids), deepcopy(rids[i]), deepcopy(rids[j])
            truncate_pair_two_candidates(cids_, rids1, rids2, self.args['max_len'])
            ids_ = [self.cls] + cids_ + [self.sep] + rids1 + [self.sep] + rids2 + [self.sep]
            tids_ = [0] * (len(cids_) + 2) + [1] * (len(rids1) + 1) + [0] * (len(rids2) + 1)
            ids.append(ids_)
            tids.append(tids_)
            recoder.append((i, j))
        ids = [torch.LongTensor(i) for i in ids]
        tids = [torch.LongTensor(i) for i in tids]
        ids = pad_sequence(ids, batch_first=True, padding_value=self.pad)
        tids = pad_sequence(tids, batch_first=True, padding_value=self.pad)
        mask = self.generate_mask(ids)
        ids, tids, mask = to_cuda(ids, tids, mask)
        # ===== make compare ===== # 
        batch_packup = {
            'ids': ids,
            'tids': tids,
            'mask': mask,
        }
        # different num_labels
        if self.args['num_labels'] == 3:
            # three classificaiton, ignore the label 1
            if self.args['mode'] == 'train':
                comp_scores = self.model.module.predict(batch_packup).max(dim=-1)[1]    # [B, 3] -> [B]
            else:
                comp_scores = self.model.predict(batch_packup).max(dim=-1)[1]    # [B, 3] -> [B]

            comp_label, new_recoder = [], []
            for s, (i, j) in zip(comp_scores, recoder):
                if s == 0:
                    comp_label.append(False)
                    new_recoder.append((i, j))
                elif s == 2:
                    comp_label.append(True)
                    new_recoder.append((i, j))
                else:
                    # hard to tell will not be used
                    pass
            return comp_label, new_recoder
        elif self.args['num_labels'] == 1:
            if self.args['mode'] == 'train':
                comp_scores = self.model.module.predict(batch_packup)    # [B]
            else:
                comp_scores = self.model.predict(batch_packup)    # [B]
        else:
            raise Exception(f'[!] donot support num_labels={self.args["num_labels"]}')

        # these modes only for bert-ft-compare
        if fast:
            return comp_scores
        elif fully is False:
            comp_label = []
            for s in comp_scores:
                if s >= 0.5 + margin:
                    comp_label.append(True)
                else:
                    comp_label.append(False)
            return comp_label, recoder
        else:
            # only for bert-ft-compare full comparsion
            comp_label = []
            for s in comp_scores:
                if s >= 0.5 + margin:
                    comp_label.append(True)
                elif s < 0.5 - margin:
                    comp_label.append(False)
            return comp_label, recoder

    @torch.no_grad()
    def fully_compare(self, batch):
        self.model.eval() 
        pos_margin = self.args['positive_margin']
        items = self.convert_text_to_ids(batch['context'] + batch['responses'])
        cids_ = items[:len(batch['context'])]
        cids = []
        for u in cids_:
            cids.extend(u + [self.eos])
        cids.pop()
        rids = items[len(batch['context']):]
        tickets = []
        for i in range(len(rids)):
            for j in range(len(rids)):
                if i < j:
                    tickets.append((i, j))
        label, recoder = self.compare_one_turn(cids, rids, tickets, margin=pos_margin, fully=True)
        # iterate to generate the scores
        # PageRank
        chain = {i: [] for i in range(len(rids))}    # key is bigger than value
        for l, (i, j) in zip(label, recoder):
            if l is True:
                chain[i].append(j)
            else:
                chain[j].append(i)
        scores = {i:1 for i in chain}
        for _ in range(5):
            new_scores = deepcopy(scores)
            for i, i_list in chain.items():
                new_scores[i] += sum([scores[j] for j in i_list])
            scores = deepcopy(new_scores)
        scores = [scores[i] for i in range(len(rids))]
        return scores

    @torch.no_grad()
    def compare_evaluation(self, test_iter):
        rest = []
        for batch in test_iter:
            scores = self.fully_compare(batch)
            c = batch['context']
            r1, r2 = batch['responses']

            items = self.convert_text_to_ids(c + [r1, r2])['input_ids']
            cids_, rids = items[0], items[1:]
            cids = []
            for u in cids_:
                cids.extend(u + [self.eos])
            cids.pop()

            tickets = [(0, 1)]
            label = self.compare_one_turn(cids, rids, tickets, margin=0, fast=True)
            label = label.tolist()[0]
            s = round(label*100, 2)
            item = {'context': c, 'responses': (r1, r2), 'score': s}
            rest.append(item)
        return rest
    
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
        pos_margin = self.args['positive_margin']
        pos_margin_delta = self.args['positive_margin_delta']

        context = batch['context']
        scores = batch['scores']

        items = self.convert_text_to_ids(context + batch['responses'])['input_ids']
        cids_, rids = items[:len(context)], items[len(context):]
        cids = []
        for u in cids_:
            cids.extend(u + [self.eos])
        cids.pop()

        # sort the rids (decrease order)
        order = np.argsort(scores)[::-1].tolist()
        backup_map = {o:i for i, o in enumerate(order)}    # old:new
        rids = [rids[i] for i in order]
        scores = [scores[i] for i in order]

        # tickets to the comparsion function
        before_dict = {i:i-1 for i in range(len(rids))}
        # first compare, second resolve the conjuction
        # each pair in tickets (i, j), i must has the front position of the j (before), which means
        # i has the higher scores than j (during the comparison, the False means the i < j,
        # and need to be swaped).
        for idx in range(compare_turn_num):
            tickets = []
            if idx == 0:
                for i in range(len(rids)):
                    if before_dict[i] != -1:
                        tickets.append((before_dict[i], i))
            else:
                # find conflict
                counter = [[] for _ in range(len(rids))]
                for i in range(len(rids)):
                    b = before_dict[i]
                    if b != -1:
                        counter[b].append(i)
                # collect confliction tickets
                for pair in counter:
                    if len(pair) == 2:
                        i, j = pair
                        if scores[i] < scores[j]:
                            tickets.append((j, i))
                        else:
                            tickets.append((i, j))
                    elif len(pair) > 2:
                        raise Exception()

                # tickets.extend(not_sure_tickets)
                # tickets = list(set(tickets))
            # abort
            if len(tickets) == 0:
                break

            label, recoder = self.compare_one_turn(cids, rids, tickets, margin=pos_margin)
            d = {j:i for l, (i, j) in zip(label, recoder) if l is False}
            d = sorted(list(d.items()), key=lambda x:x[0])    # left to right

            # not_sure_tickets = []

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

                # not sure
                # if before_dict[j] != -1:
                #     not_sure_tickets.append((before_dict[j], j))
            # changing becomer harder and harder
            pos_margin -= pos_margin_delta

        # backup the scores
        scores = [scores[backup_map[i]] for i in range(len(order))]
        return scores

    def convert_text_to_ids(self, texts):
        items = self.vocab.batch_encode_plus(texts, add_special_tokens=False)['input_ids']
        return items
