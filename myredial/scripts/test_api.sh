#!/bin/bash

mode=$1
dataset=$2
python test_api.py \
    --size 10 \
    --url 9.91.66.241 \
    --port 8096 \
    --mode $mode \
    --dataset $dataset \
    --topk 10 \
    --seed 0 \
    --block_size 1
