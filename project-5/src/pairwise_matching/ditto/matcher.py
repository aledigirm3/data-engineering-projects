import torch
import torch.nn as nn
import os
import numpy as np
import random
import json
import jsonlines
import csv
import re
import time
import argparse
import sys
import sklearn
import traceback

from torch.utils import data
from tqdm import tqdm
from scipy.special import softmax

from ditto_light.ditto import evaluate, DittoModel
from ditto_light.exceptions import ModelNotFoundError
from ditto_light.dataset import DittoDataset
from ditto_light.summarize import Summarizer
from ditto_light.knowledge import *

def set_seed(seed: int):
    """
    Helper function for reproducible behavior to set the seed in ``random``, ``numpy``, ``torch``
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def to_str(ent1, ent2, summarizer=None, max_len=256, dk_injector=None):
    """Serialize a pair of data entries

    Args:
        ent1 (Dictionary): the 1st data entry
        ent2 (Dictionary): the 2nd data entry
        summarizer (Summarizer, optional): the summarization module
        max_len (int, optional): the max sequence length
        dk_injector (DKInjector, optional): the domain-knowledge injector

    Returns:
        string: the serialized version
    """
    content = ''
    for ent in [ent1, ent2]:
        if isinstance(ent, str):
            content += ent
        else:
            for attr in ent.keys():
                content += 'COL %s VAL %s ' % (attr, ent[attr])
        content += ' || '

    content += '0'

    if summarizer is not None:
        content = summarizer.transform(content, max_len=max_len)

    new_ent1, new_ent2, _ = content.split(' || ')
    if dk_injector is not None:
        new_ent1 = dk_injector.transform(new_ent1)
        new_ent2 = dk_injector.transform(new_ent2)

    return new_ent1 + ' || ' + new_ent2 + ' || 0'


def classify(sentence_pairs, model,
             lm='distilbert',
             max_len=256,
             threshold=None):
    """Apply the MRPC model.

    Args:
        sentence_pairs (list of str): the sequence pairs
        model (MultiTaskNet): the model in pytorch
        max_len (int, optional): the max sequence length
        threshold (float, optional): the threshold of the 0's class

    Returns:
        list of float: the scores of the pairs
    """
    inputs = sentence_pairs
    # print('max_len =', max_len)
    dataset = DittoDataset(inputs,
                           max_len=max_len,
                           lm=lm)
    # print(dataset[0])
    iterator = data.DataLoader(dataset=dataset,
                               batch_size=len(dataset),
                               shuffle=False,
                               num_workers=0,
                               collate_fn=DittoDataset.pad)

    # prediction
    all_probs = []
    all_logits = []
    with torch.no_grad():
        # print('Classification')
        for i, batch in enumerate(iterator):
            x, _ = batch
            logits = model(x)
            probs = logits.softmax(dim=1)[:, 1]
            all_probs += probs.cpu().numpy().tolist()
            all_logits += logits.cpu().numpy().tolist()

    if threshold is None:
        threshold = 0.5

    pred = [1 if p > threshold else 0 for p in all_probs]
    return pred, all_logits

def predict(input_path, output_path, config,
            model,
            batch_size=2048,
            summarizer=None,
            lm='distilbert',
            max_len=256,
            dk_injector=None,
            threshold=None,
            pairs_to_evaluate=None):
    """Run the model over the input file containing the candidate entry pairs

    Args:
        input_path (str): the input file path
        output_path (str): the output file path
        config (Dictionary): task configuration
        model (DittoModel): the model for prediction
        batch_size (int): the batch size
        summarizer (Summarizer, optional): the summarization module
        max_len (int, optional): the max sequence length
        dk_injector (DKInjector, optional): the domain-knowledge injector
        threshold (float, optional): the threshold of the 0's class

    Returns:
        None
    """
    pairs = []
    
    def process_batch(rows, pairs, writer=None):
        predictions, logits = classify(pairs, model, lm=lm,
                                       max_len=max_len,
                                       threshold=threshold)
        scores = softmax(logits, axis=1)
        results = []
        for row, pred, score in zip(rows, predictions, scores):
            if writer is not None:
                output = {'left': row[0], 'right': row[1],
                    'match': pred,
                    'match_confidence': score[int(pred)]}
                writer.write(output)
            results.append((row[0], row[1], pred))
        return results

    if pairs_to_evaluate is not None:
        start_time = time.time()
        eval_results = []
        
        # remove roba
        pairs_to_evaluate = [ (re.sub(r'\[.*?\]', '', x).strip(), re.sub(r'\[.*?\]', '', y).strip()) for x, y in pairs_to_evaluate ]
        
        for i in range(0, len(pairs_to_evaluate), batch_size):
            batch = pairs_to_evaluate[i:i + batch_size]
            eval_results.extend(process_batch(batch, [ f"{item1} || {item2}" for item1, item2 in batch ]))
        
        with open(output_path, 'w', encoding="utf8") as f:
            for pair in eval_results:
                f.write(f"{pair[0]} || {pair[1]} || {pair[2]}\n")
        run_time = time.time() - start_time
        run_tag = '%s_lm=%s_dk=%s_su=%s' % (config['name'], lm, str(dk_injector != None), str(summarizer != None))
        os.system('echo %s %f >> log_ditto.txt' % (run_tag, run_time))
        
        return
    
    # input_path can also be train/valid/test.txt
    # convert to jsonlines
    if '.txt' in input_path:
        with jsonlines.open(input_path + '.jsonl', mode='w') as writer:
            for line in open(input_path):
                writer.write(line.split(' || ')[:2])
        input_path += '.jsonl'

    # batch processing
    start_time = time.time()
    with jsonlines.open(input_path) as reader,\
         jsonlines.open(output_path, mode='w') as writer:
        pairs = []
        rows = []
        for idx, row in tqdm(enumerate(reader)):
            pairs.append(to_str(row[0], row[1], summarizer, max_len, dk_injector))
            rows.append(row)
            if len(pairs) == batch_size:
                process_batch(rows, pairs, writer)
                pairs.clear()
                rows.clear()

        if len(pairs) > 0:
            process_batch(rows, pairs, writer)

    run_time = time.time() - start_time
    run_tag = '%s_lm=%s_dk=%s_su=%s' % (config['name'], lm, str(dk_injector != None), str(summarizer != None))
    os.system('echo %s %f >> log_ditto.txt' % (run_tag, run_time))


def tune_threshold(config, model, hp):
    """Tune the prediction threshold for a given model on a validation set"""
    validset = config["ditto_path"] + config['validset']
    task = hp.task

    # summarize the sequences up to the max sequence length
    set_seed(123)
    summarizer = injector = None
    if hp.summarize:
        summarizer = Summarizer(config, lm=hp.lm)
        validset = summarizer.transform_file(validset, max_len=hp.max_len, overwrite=True)

    if hp.dk is not None:
        if hp.dk == 'product':
            injector = ProductDKInjector(config, hp.dk)
        else:
            injector = GeneralDKInjector(config, hp.dk)

        validset = injector.transform_file(validset)

    # load dev sets
    valid_dataset = DittoDataset(validset,
                                 max_len=hp.max_len,
                                 lm=hp.lm)

    # print(valid_dataset[0])

    valid_iter = data.DataLoader(dataset=valid_dataset,
                                 batch_size=64,
                                 shuffle=False,
                                 num_workers=0,
                                 collate_fn=DittoDataset.pad)

    # acc, prec, recall, f1, v_loss, th = eval_classifier(model, valid_iter,
    #                                                     get_threshold=True)
    f1, th = evaluate(model, valid_iter, threshold=None)

    # verify F1
    set_seed(123)
    predict(validset, "tmp.jsonl", config, model,
            summarizer=summarizer,
            max_len=hp.max_len,
            lm=hp.lm,
            dk_injector=injector,
            threshold=th)

    predicts = []
    with jsonlines.open("tmp.jsonl", mode="r") as reader:
        for line in reader:
            predicts.append(int(line['match']))
    os.system("rm tmp.jsonl")

    labels = []
    with open(validset) as fin:
        for line in fin:
            labels.append(int(line.split(' || ')[-1]))

    real_f1 = sklearn.metrics.f1_score(labels, predicts)
    print("load_f1 =", f1)
    print("real_f1 =", real_f1)

    return th



def load_model(task, path, lm, use_gpu, fp16=True, ditto_path: str = './'):
    """Load a model for a specific task.

    Args:
        task (str): the task name
        path (str): the path of the checkpoint directory
        lm (str): the language model
        use_gpu (boolean): whether to use gpu
        fp16 (boolean, optional): whether to use fp16

    Returns:
        Dictionary: the task config
        MultiTaskNet: the model
    """
    # load models
    checkpoint = os.path.join(path, task, 'model.pt')
    if not os.path.exists(checkpoint):
        raise ModelNotFoundError(checkpoint)

    configs = json.load(open(ditto_path + 'configs.json'))
    configs = {conf['name'] : conf for conf in configs}
    config = configs[task]
    config["ditto_path"] = ditto_path
    config_list = [config]

    if use_gpu:
        print("USING CUDAAA")
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    else:
        device = 'cpu'

    model = DittoModel(device=device, lm=lm)

    saved_state = torch.load(checkpoint, map_location=lambda storage, loc: storage)
    model.load_state_dict(saved_state['model'])
    model = model.to(device)

    return config, model


def pairwise_matching(task='companies',
         input_path='../../blocking/results/lsh_bigram_blocking.json',
         output_path='../results/ditto/ditto_predict_lsh_bigram_blocking.txt',
         lm='distilbert',
         use_gpu=False,
         fp16=False,
         checkpoint_path='checkpoints/',
         dk=None,
         summarize=False,
         max_len=256,
         pairs_to_evaluate=None,
         ditto_path: str = './',
         gt_eval=False):
    
    # Load the models
    set_seed(123)
    config, model = load_model(task, checkpoint_path, lm, use_gpu, fp16, ditto_path)

    summarizer = dk_injector = None
    if summarize:
        summarizer = Summarizer(config, lm)

    if dk is not None:
        if 'product' in dk:
            dk_injector = ProductDKInjector(config, dk)
        else:
            dk_injector = GeneralDKInjector(config, dk)

    # Tune threshold
    threshold = tune_threshold(config, model, argparse.Namespace(
        task=task, input_path=input_path, output_path=output_path, lm=lm,
        use_gpu=use_gpu, fp16=fp16, checkpoint_path=checkpoint_path, dk=dk,
        summarize=summarize, max_len=max_len
    ))

    if gt_eval:
        batch_size = 32
    else:
        batch_size = 2048
        
    # Run prediction
    predict(input_path, output_path, config, model,
            summarizer=summarizer,
            max_len=max_len,
            lm=lm,
            dk_injector=dk_injector,
            threshold=threshold,
            pairs_to_evaluate=pairs_to_evaluate,
            batch_size=batch_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, default='companies')
    parser.add_argument("--input_path", type=str, default='../../blocking/results/lsh_bigram_blocking.json')
    parser.add_argument("--output_path", type=str, default='../results/ditto/ditto_predict_lsh_bigram_blocking.txt')
    parser.add_argument("--lm", type=str, default='distilbert')
    parser.add_argument("--use_gpu", action="store_true")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--checkpoint_path", type=str, default='checkpoints/')
    parser.add_argument("--dk", type=str, default=None)
    parser.add_argument("--summarize", action="store_true")
    parser.add_argument("--max_len", type=int, default=256)
    
    args = parser.parse_args()
    
    pairwise_matching(args.task, args.input_path, args.output_path, args.lm,
         args.use_gpu, args.fp16, args.checkpoint_path, args.dk,
         args.summarize, args.max_len)

