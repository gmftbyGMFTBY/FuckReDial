#!/bin/bash

# ========== How to run this script ========== #
# ./run.sh <train/test/train-post> <dataset_name> <model_name> <cuda_ids>
# for example: ./run/sh train train_generative gpt2 0,1,2,3

# ========== metadata ========== #
max_len=256
res_max_len=64
seed=50
epoch=5
bsz=32
head_num=5
pre_extract=500
inf_bsz=64
post_bsz=32
post_epoch=5
post_max_len=512
post_res_max_len=64
neg_bsz=64    # useless
total_steps=100000
warmup_ratio=0.1
test_batch_size=32
models=(hash-bert dual-bert-ma sa-bert-neg dual-bert-writer dual-bert-kw dual-bert-semi dual-bert-mlm dual-bert-cross dual-bert-scm sa-bert bert-ft bert-ft-multi bert-gen bert-gen-ft bert-post dual-bert-fg dual-bert-gen dual-bert dual-bert-poly dual-bert-cl dual-bert-vae dual-bert-vae2 dual-bert-one2many dual-bert-hierarchical dual-bert-mb dual-bert-adv dual-bert-jsd dual-bert-hierarchical-trs dual-gru-hierarchical-trs)
ONE_BATCH_SIZE_MODEL=(hash-bert dual-bert-ma dual-bert-writer dual-bert-kw dual-bert-semi dual-bert-mlm dual-bert-cross dual-bert-scm bert-ft-multi dual-bert dual-bert-poly dual-bert-fg dual-bert-cl dual-bert-gen dual-bert-vae dual-bert-vae2 dual-bert-one2many dual-bert-hierarchical dual-bert-hierarchical-trs dual-bert-mb dual-bert-adv dual-bert-jsd dual-gru-hierarchical)
datasets=(ecommerce douban ubuntu lccc lccc-large writer)
chinese_datasets=(douban ecommerce lccc lccc-large writer)
# ========== metadata ========== #

mode=$1
dataset=$2
model=$3
cuda=$4 

if [[ ${chinese_datasets[@]} =~ $dataset ]]; then
    pretrained_model=bert-base-chinese
    lang=zh
else
    pretrained_model=bert-base-uncased
    lang=en
fi

if [ $mode = 'init' ]; then
    mkdir bak ckpt rest
    for m in ${models[@]}
    do
        for d in ${datasets[@]}
        do
            mkdir -p ckpt/$d/$m
            mkdir -p rest/$d/$m
            mkdir -p bak/$d/$m
        done
    done
elif [ $mode = 'backup' ]; then
    cp ckpt/$dataset/$model/param.txt bak/$dataset/$model/
    cp ckpt/$dataset/$model/best.pt bak/$dataset/$model/
    cp rest/$dataset/$model/rest.txt bak/$dataset/$model/
    cp rest/$dataset/$model/events* bak/$dataset/$model/
elif [ $mode = 'train' ]; then
    ./run.sh backup $dataset $model
    rm ckpt/$dataset/$model/*
    rm rest/$dataset/$model/events*    # clear the tensorboard cache

    if [ $model = 'dual-gru-hierarchical-trs' ]; then
        pretrained_model_path=data/$dataset/word2vec.pt
    else
        pretrained_model_path=''
    fi
    
    if [[ ${ONE_BATCH_SIZE_MODEL[@]} =~ $model ]]; then
        test_bsz=1
    else
        test_bsz=$test_batch_size
    fi
    
    gpu_ids=(${cuda//,/ })
    CUDA_VISIBLE_DEVICES=$cuda python -m torch.distributed.launch --nproc_per_node=${#gpu_ids[@]} --master_addr 127.0.0.1 --master_port 29406 main.py \
        --dataset $dataset \
        --model $model \
        --mode train \
        --batch_size $bsz \
        --test_batch_size $test_bsz \
        --neg_bsz $bsz \
        --epoch $epoch \
        --seed $seed \
        --max_len $max_len \
        --res_max_len $res_max_len \
        --multi_gpu $cuda \
        --pretrained_model $pretrained_model \
        --head_num $head_num \
        --lang $lang \
        --total_steps $total_steps \
        --warmup_ratio $warmup_ratio
        # --pretrained_model_path $pretrained_model_path
elif [ $mode = 'inference_qa' ]; then
    gpu_ids=(${cuda//,/ })
    CUDA_VISIBLE_DEVICES=$cuda python -m torch.distributed.launch --nproc_per_node=${#gpu_ids[@]} --master_addr 127.0.0.1 --master_port 29400 main.py \
        --dataset $dataset \
        --model $model \
        --mode inference_qa \
        --batch_size $inf_bsz \
        --seed $seed \
        --max_len $max_len \
        --multi_gpu $cuda \
        --pretrained_model $pretrained_model \
        --lang $lang \
        --warmup_ratio $warmup_ratio
    # reconstruct
    python searcher.py \
        --mode inference_qa \
        --dataset $dataset \
        --nums ${#gpu_ids[@]} \
        --inner_bsz 128 \
        --pre_extract $pre_extract \
        --topk 100
elif [ $mode = 'inference' ]; then
    gpu_ids=(${cuda//,/ })
    CUDA_VISIBLE_DEVICES=$cuda python -m torch.distributed.launch --nproc_per_node=${#gpu_ids[@]} --master_addr 127.0.0.1 --master_port 29400 main.py \
        --dataset $dataset \
        --model $model \
        --mode inference \
        --batch_size $inf_bsz \
        --seed $seed \
        --max_len $max_len \
        --multi_gpu $cuda \
        --pretrained_model $pretrained_model \
        --lang $lang \
        --warmup_ratio $warmup_ratio
    # reconstruct
    python searcher.py \
        --mode inference \
        --dataset $dataset \
        --nums ${#gpu_ids[@]} \
        --inner_bsz 128 \
        --pre_extract $pre_extract \
        --topk 100
elif [ $mode = 'train-post' ]; then
    # load model parameters from post train checkpoint and fine tuning
    ./run.sh backup $dataset $model
    rm ckpt/$dataset/$model/*
    rm rest/$dataset/$model/events*    # clear the tensorboard cache
    
    if [[ ${ONE_BATCH_SIZE_MODEL[@]} =~ $model ]]; then
        test_bsz=1
    else
        test_bsz=$test_batch_size
    fi
    
    gpu_ids=(${cuda//,/ })
    CUDA_VISIBLE_DEVICES=$cuda python -m torch.distributed.launch --nproc_per_node=${#gpu_ids[@]} --master_addr 127.0.0.1 --master_port 29406 main.py \
        --dataset $dataset \
        --model $model \
        --mode train-post \
        --batch_size $post_bsz \
        --test_batch_size $test_bsz \
        --neg_bsz $bsz \
        --epoch $post_epoch \
        --seed $seed \
        --max_len $post_max_len \
        --res_max_len $post_res_max_len \
        --multi_gpu $cuda \
        --pretrained_model $pretrained_model \
        --warmup_ratio $warmup_ratio \
        --total_steps $total_steps \
        --pretrained_model_path ckpt/$dataset/bert-post/best_nspmlm.pt
elif [ $mode = 'train-dual-post' ]; then
    echo "[!] make sure that the dual bert has been already trained on bert-post checkpoint"
    # load model parameters from post train checkpoint and fine tuning
    ./run.sh backup $dataset $model
    rm ckpt/$dataset/$model/*
    rm rest/$dataset/$model/events*    # clear the tensorboard cache
    
    if [[ ${ONE_BATCH_SIZE_MODEL[@]} =~ $model ]]; then
        test_bsz=1
    else
        test_bsz=$test_batch_size
    fi
    
    gpu_ids=(${cuda//,/ })
    CUDA_VISIBLE_DEVICES=$cuda python -m torch.distributed.launch --nproc_per_node=${#gpu_ids[@]} --master_addr 127.0.0.1 --master_port 29403 main.py \
        --dataset $dataset \
        --model $model \
        --mode train-dual-post \
        --batch_size $post_bsz \
        --test_batch_size $test_bsz \
        --neg_bsz $bsz \
        --epoch $post_epoch \
        --seed $seed \
        --max_len $post_max_len \
        --res_max_len $post_res_max_len \
        --multi_gpu $cuda \
        --pretrained_model $pretrained_model \
        --warmup_ratio $warmup_ratio \
        --total_steps $total_steps \
        --pretrained_model_path ckpt/$dataset/dual-bert/best.pt
else
    # test
    if [[ ${ONE_BATCH_SIZE_MODEL[@]} =~ $model ]]; then
        test_bsz=1
    else
        test_bsz=$test_batch_size
    fi
    CUDA_VISIBLE_DEVICES=$cuda python main.py \
        --dataset $dataset \
        --model $model \
        --mode $mode \
        --test_batch_size $test_bsz \
        --max_len $max_len \
        --res_max_len $res_max_len \
        --seed $seed \
        --multi_gpu $cuda \
        --head_num $head_num \
        --lang $lang \
        --pretrained_model $pretrained_model
fi
