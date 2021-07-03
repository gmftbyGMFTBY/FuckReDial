import http.client
import torch
import random
import numpy as np
from tqdm import tqdm
import pprint
import json
import ipdb
from dataloader import *
from config import *
import argparse

def parser_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--size', type=int, default=1000)
    parser.add_argument('--block_size', type=int, default=10)
    parser.add_argument('--topk', type=int, default=10, help='topk candidates for recall')
    parser.add_argument('--mode', type=str, default='rerank/recall/pipeline')
    parser.add_argument('--url', type=str, default='9.91.66.241')
    parser.add_argument('--port', type=int, default=22335)
    parser.add_argument('--dataset', type=str, default='douban')
    return parser.parse_args()

def load_fake_rerank_data(path, size=1000):
    # make sure the data reader
    dataset, _ = read_json_data(path, lang='zh')
    data = []
    cache, block_size = [], random.randint(1, args['block_size'])
    current_num = 0
    for i in dataset:
        if current_num == block_size:
            data.append({
                'segment_list': [
                    {
                        'context': ' [SEP] '.join(j[0]), 
                        'candidates': [j[1]] + j[2]
                    } for j in cache
                ],
                'lang': 'zh',
            })
            current_num, cache = 1, [i]
            block_size = random.randint(1, args['block_size'])
        else:
            current_num += 1
            cache.append(i)
    data = random.sample(data, size)
    return data

def load_fake_recall_data(path, size=1000):
    '''for pipeline and recall test'''
    if args['dataset'] in ['douban', 'ecommerce', 'ubuntu', 'lccc', 'lccc-large']:
        dataset = read_text_data_utterances(path, lang='zh')
        dataset = [(utterances[:-1], utterances[-1], None) for _, utterances in dataset]
    elif args['dataset'] in ['poetry', 'novel_selected']:
        dataset = read_text_data_with_source(path, lang='zh')
    else:
        dataset, _ = read_json_data(path, lang='zh')
    data = []
    cache, block_size = [], random.randint(1, args['block_size'])
    current_num = 0
    for i in tqdm(dataset):
        if current_num == block_size:
            data.append({
                'segment_list': [
                    {
                        'str': ' [SEP] '.join(j[0]), 
                        'status': 'editing'
                    } for j in cache
                ],
                'lang': 'zh',
                'topk': args['topk'],
            })
            current_num, cache = 1, [i]
            block_size = random.randint(1, args['block_size'])
        else:
            current_num += 1
            cache.append(i)
    data = random.sample(data, size)
    return data

def SendPOST(url, port, method, params):
    '''
    import http.client

    parameters:
        1. url: 9.91.66.241
        2. port: 8095
        3. method:  /rerank or /recall
        4. params: json dumps string
    '''
    headers = {"Content-type": "application/json"}
    conn = http.client.HTTPConnection(url, port)
    conn.request('POST', method, params, headers)
    response = conn.getresponse()
    code = response.status
    reason=response.reason
    data = json.loads(response.read().decode('utf-8'))
    conn.close()
    return data

def test_recall(args):
    data = load_fake_recall_data(
        f'{args["root_dir"]}/data/{args["dataset"]}/test.txt',
        size=args['size'],
    )
    # recall test begin
    avg_times = []
    collections = []
    error_counter = 0
    pbar = tqdm(data)
    for data in pbar:
        data = json.dumps(data)
        rest = SendPOST(args['url'], args['port'], '/recall', data)
        if rest['header']['ret_code'] == 'fail':
            error_counter += 1
        else:
            collections.append(rest)
            avg_times.append(rest['header']['core_time_cost_ms'])
        pbar.set_description(f'[!] time: {round(np.mean(avg_times), 2)} ms; error: {error_counter}')
    avg_t = round(np.mean(avg_times), 4)
    print(f'[!] avg recall time cost: {avg_t} ms; error ratio: {round(error_counter/len(data), 4)}')
    return collections


def test_rerank(args):
    data = load_fake_rerank_data(
        f'{args["root_dir"]}/data/{args["dataset"]}/test.txt',
        size=args['size'],
    )
    # rerank test begin
    avg_times = []
    collections = []
    error_counter = 0
    pbar = tqdm(data)
    for data in pbar:
        data = json.dumps(data)
        rest = SendPOST(args['url'], args['port'], '/rerank', data)
        if rest['header']['ret_code'] == 'fail':
            error_counter += 1
        else:
            collections.append(rest)
            avg_times.append(rest['header']['core_time_cost_ms'])
        pbar.set_description(f'[!] time: {round(np.mean(avg_times), 2)} ms; error: {error_counter}')
    avg_t = round(np.mean(avg_times), 4)
    print(f'[!] avg rerank time cost: {avg_t} ms; error ratio: {round(error_counter/len(data), 4)}')
    return collections

def test_pipeline(args):
    data = load_fake_recall_data(
        f'{args["root_dir"]}/data/{args["dataset"]}/test.txt',
        size=args['size'],
    )
    # pipeline test begin
    avg_times = []
    collections = []
    error_counter = 0
    pbar = tqdm(list(enumerate(data)))
    for idx, data in pbar:
        data = json.dumps(data)
        rest = SendPOST(args['url'], args['port'], '/pipeline', data)
        if rest['header']['ret_code'] == 'fail':
            error_counter += 1
            print(f'[!] ERROR happens in sample {idx}')
        else:
            collections.append(rest)
            avg_times.append(rest['header']['core_time_cost_ms'])
        pbar.set_description(f'[!] time: {round(np.mean(avg_times), 2)} ms; error: {error_counter}')
    avg_t = round(np.mean(avg_times), 4)
    print(f'[!] avg rerank time cost: {avg_t} ms; error ratio: {round(error_counter/len(data), 4)}')
    return collections


if __name__ == '__main__':
    args = vars(parser_args())
    args['root_dir'] = '/apdcephfs/share_916081/johntianlan/MyReDial'
    MAP = {
        'recall': test_recall,
        'rerank': test_rerank,
        'pipeline': test_pipeline,
    }
    collections = MAP[args['mode']](args)
    
    # write into log file
    write_path = f'{args["root_dir"]}/data/{args["dataset"]}/test_api_{args["mode"]}_log.txt'
    with open(write_path, 'w') as f:
        for sample in tqdm(collections):
            data = sample['item_list']
            if sample['header']['ret_code'] == 'fail':
                continue
            if args['mode'] == 'pipeline':
                for item in data:
                    f.write(f'[Context ] {item["context"]}\n')
                    f.write(f'[Response] {item["response"]}\n\n')
            elif args['mode'] == 'recall':
                for item in data:
                    f.write(f'[Context] {item["context"]}\n')
                    for idx, neg in enumerate(item['candidates']):
                        f.write(f'[Cands-{idx}] {neg["text"]}\n')
                    f.write('\n')
            elif args['mode'] == 'rerank':
                for item in data:
                    f.write(f'[Context] {item["context"]}\n')
                    for i in item['candidates']:
                        f.write(f'[Score {round(i["score"], 2)}] {i["str"]}\n')
                    f.write('\n')
            else:
                raise Exception(f'[!] Unkown mode: {args["mode"]}')

    print(f'[!] write the log into file: {write_path}')
