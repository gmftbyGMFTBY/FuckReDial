from model.utils import *

class BERTDualCompEncoder(nn.Module):

    '''This model needs the gray(hard negative) samples, which cannot be used for recall'''
    
    def __init__(self, **args):
        super(BERTDualCompEncoder, self).__init__()
        model = args['pretrained_model']
        s = args['smoothing']
        self.gray_num = args['gray_cand_num']
        nhead = args['nhead']
        dim_feedforward = args['dim_feedforward']
        dropout = args['dropout']
        num_encoder_layers = args['num_encoder_layers']

        self.loss1_w = args['loss1_weight']
        self.loss2_w = self.loss1_w / self.gray_num
        self.loss3_w = args['loss3_weight']

        # ====== Model ====== #
        self.ctx_encoder = BertEmbedding(model=model)
        self.can_encoder = BertEmbedding(model=model)

        hidden_size = self.ctx_encoder.model.config.hidden_size
        encoder_layer = nn.TransformerEncoderLayer(
            hidden_size*2, 
            nhead=nhead, 
            dim_feedforward=dim_feedforward, 
            dropout=dropout,
        )
        encoder_norm = nn.LayerNorm(2*hidden_size)
        self.trs_encoder = nn.TransformerEncoder(
            encoder_layer,
            num_encoder_layers, 
            encoder_norm,
        )
        self.trs_head = nn.Sequential(
            self.trs_encoder,
            nn.Tanh(),
            nn.Dropout(p=dropout),
            nn.Linear(hidden_size*2, hidden_size),
        )
        self.cls_head = nn.Sequential(
            nn.Linear(hidden_size*2, hidden_size),
            nn.Tanh(),
            nn.Dropout(p=dropout),
            nn.Linear(hidden_size, hidden_size),
        )

    def _encode(self, cid, rid, cid_mask, rid_mask):
        cid_rep = self.ctx_encoder(cid, cid_mask)
        rid_rep = self.can_encoder(rid, rid_mask)
        b_c = len(cid_rep)
        rid_reps = rid_rep.unsqueeze(0).repeat(b_c, 1, 1)    # [B_c, B_r*gray, E]
        # fuse context into the response
        cid_reps = cid_rep.unsqueeze(1).repeat(1, len(rid), 1)    # [B_c, B_r*gray, E]
        for_comp = torch.cat([rid_reps, cid_reps], dim=-1)   # [B_c, B_r*gray, 2*E]
        comp_reps = self.trs_head(for_comp.permute(1, 0, 2)).permute(1, 0, 2)    # [B, G, E] 

        rid_reps = torch.cat([rid_reps, comp_reps], dim=-1)    # [B, G, 2*E]
        rid_reps = self.cls_head(rid_reps).permute(1, 0, 2)    # [B, G, E] -> [G, B, E]
        return cid_rep, rid_reps 

    @torch.no_grad()
    def get_cand(self, ids, attn_mask):
        rid_rep = self.can_encoder(ids, attn_mask)
        return rid_rep

    @torch.no_grad()
    def get_ctx(self, ids, attn_mask):
        cid_rep = self.ctx_encoder(ids, attn_mask)
        return cid_rep

    @torch.no_grad()
    def predict(self, batch):
        cid = batch['ids']
        rid = batch['rids']
        rid_mask = batch['rids_mask']
        cid = cid.unsqueeze(0)
        cid_mask = torch.ones_like(cid)

        batch_size = rid.shape[0]
        cid_rep, rid_reps = self._encode(cid, rid, cid_mask, rid_mask)
        dot_product = torch.einsum('ijk,jk->ij', rid_reps, cid_rep).t()    # [B, G]
        dot_product = dot_product.squeeze(0)    # [G]
        return dot_product
    
    def forward(self, batch):
        cid = batch['ids']
        rid = batch['rids']
        cid_mask = batch['ids_mask']
        rid_mask = batch['rids_mask']

        b_c, b_r = len(cid), int(len(rid)//(self.gray_num+1))
        assert b_c == b_r
        cid_rep, rid_reps = self._encode(cid, rid, cid_mask, rid_mask)    # [G, B, E]/[B, E]
        dot_product = torch.einsum('ijk,jk->ij', rid_reps, cid_rep).t()    # [B, G]

        # postive samples vs. all the negative samples
        mask = torch.zeros_like(dot_product)
        mask[torch.arange(b_c), torch.arange(0, len(rid), self.gray_num+1)] = 1.
        loss_ = F.log_softmax(dot_product, dim=-1) * mask
        loss1 = (-loss_.sum(dim=1)).mean()

        # hard negative vs. random negative samples
        loss2 = 0
        for i in range(1, self.gray_num+1):
            matrix = []
            for j in range(b_c):
                index = list(range(len(rid)))
                # remove 
                for idx in range(j*(1+self.gray_num), (j+1)*(1+self.gray_num)):
                    index.remove(idx)
                index = [j*(1+self.gray_num) + i] + index
                row = dot_product[j, index]
                matrix.append(row)
            matrix = torch.stack(matrix)    # [B, G']
            mask = torch.zeros_like(matrix)
            mask[:, 0] = 1.
            loss_ = F.log_softmax(matrix, dim=-1) * mask
            loss2 += (-loss_.sum(dim=1)).mean()

        # positive samples vs. hard negative samples
        matrix = []
        for j in range(b_c):
            index = list(range(j*(1+self.gray_num), (j+1)*(1+self.gray_num)))
            row = dot_product[j, index]
            matrix.append(row)
        matrix = torch.stack(matrix)
        mask = torch.zeros_like(matrix)
        mask[:, 0] = 1.
        loss_ = F.log_softmax(matrix, dim=-1) * mask
        loss3 = (-loss_.sum(dim=1)).mean()

        # total loss
        loss = self.loss1_w * loss1 + self.loss2_w * loss2 + self.loss3_w * loss3

        # acc
        acc_num = (F.softmax(dot_product, dim=-1).max(dim=-1)[1] == torch.LongTensor(torch.arange(0, len(rid), self.gray_num+1)).cuda()).sum().item()
        acc = acc_num / b_c

        return loss, acc
