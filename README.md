## Cross-encoder models performance

Parameters reference: [TODO](https://github.com/taesunwhang/UMS-ResSel/blob/635e37f5340faf5a37f3b1510a9402be18348c66/config/hparams.py)

### 1. E-Commerce Dataset

| Original      | R10@1 | R10@2 | R10@5 | MRR   |
| ------------- | ----- | ----- | ----- | ----- |
| BERT-FT       | 62.3  | 84.2  | 98    | 77.59 |
| BERT-Gen-FT   | 63.3  | 83.5  | 97.1  | 77.71 |
| BERT-Gen-FT w/o Gen | | | | |

| Adversarial   | R10@1 | R10@2 | R10@5 | MRR    |
| ------------- | ----- | ----- | ----- | ------ |
| BERT-FT       | 37.4  | 73.4  | 97.6  | 62.84  |
| BERT-Gen-FT   | 44.1  | 74.8  | 96.1  | 66.23  |
| BERT-Gen-FT w/o Gen | | | | |

### 2. Douban Dataset

| Original      | R10@1 | R10@2 | R10@5 | MRR   |  P@1  |  MAP  |
| ------------- | ----- | ----- | ----- | ----- | ----- | ----- |
| BERT-FT       | 25.86 | 44.63 | 83.43 | 61.55 | 42.58 | 57.59 |
| BERT-Gen-FT   |  |  |  |  |       |       |
| BERT-Gen-FT w/o Gen | | | | |

| Adversarial   | R10@1 | R10@2 | R10@5 | MRR    |  P@1  | MAP  |
| ------------- | ----- | ----- | ----- | ------ | ----- | ---- |
| BERT-FT       |  |  |  |  |       |      |
| BERT-Gen-FT   |  |  |  |  |       |      |
| BERT-Gen-FT w/o Gen | | | | |