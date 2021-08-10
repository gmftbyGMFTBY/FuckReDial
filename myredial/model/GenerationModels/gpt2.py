from model.utils import *
from .utils import *


class GPT2Model(nn.Module):

    def __init__(self, **args):
        super(GPT2Model, self).__init__()
        model = args['pretrained_model']
        self.model = GPT2LMHeadModel.from_pretrained(model)
        # pad token is 0
        self.gen_loss_fct = nn.CrossEntropyLoss(ignore_index=0)
        self.vocab = BertTokenizerFast.from_pretrained(model)
        self.pad, self.bos, self.eos = self.vocab.convert_tokens_to_ids(['[PAD]', '[CLS]', 'SEP'])
        self.topk = args['topk']
        self.topp = args['topp']
        self.temp = args['temp']
        self.max_len = args['max_len']
        self.min_len = args['min_len']

    @torch.no_grad()
    def calculate_ppl(self, ids, ids_mask):
        gen_logits = self.model(input_ids=ids, attention_mask=ids_mask)
        gen_logits = gen_logits.logits
        shift_logits = gen_logits[..., :-1, :].contiguous()
        shift_labels = ids[..., 1:].contiguous()
        loss = self.gen_loss_fct(
            shift_logits.view(-1, shift_logits.size(-1)), 
            shift_labels.view(-1)
        )
        ppl = math.exp(loss.item())
        return ppl

    @torch.no_grad()
    def predict(self, batch):
        ids = batch['ids']
        ids_mask = batch['mask']
        logits = self.model.generate(
            input_ids=ids, 
            attention_mask=ids_mask,
            pad_token_id=self.pad,
            bos_token_id=self.bos,
            eos_token_id=self.eos,
            top_k=self.topk,
            top_p=self.topp,
            temperature=self.temp,
            forced_eos_token_id=True,
            do_sampling=True,
            max_length=self.max_len,
            min_length=self.min_len,
        )
        return logits

    def forward(self, batch):
        ids = batch['ids']
        ids_mask = batch['mask']

        batch_size = ids.shape[0]
        # [B, S, V]
        gen_logits = self.model(input_ids=ids, attention_mask=ids_mask)
        gen_logits = gen_logits.logits

        # generative loss
        # gen_logits: [B, S, V]; label: [B, S]
        shift_logits = gen_logits[..., :-1, :].contiguous()
        shift_labels = ids[..., 1:].contiguous()
        loss = self.gen_loss_fct(
            shift_logits.view(-1, shift_logits.size(-1)), 
            shift_labels.view(-1)
        )

        # token acc
        chosen_tokens = torch.max(shift_logits, dim=-1)[1]    # [B, S-1]
        gen_acc = (chosen_tokens.view(-1) == shift_labels.view(-1)).to(torch.long)
        valid_mask = (shift_labels != 0).view(-1)
        valid_tokens = gen_acc & valid_mask
        gen_acc = valid_tokens.sum().item() / valid_mask.sum().item()
        return loss, gen_acc
