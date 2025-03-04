import json
import sys
import os
from itertools import combinations
import re

### --------- ###
prv_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(prv_folder)
import paths
from ansi_colors import *
### --------- ###

def normalize_pair(pair: tuple):
    return tuple(sorted((re.sub(r'\[.*?\]', '', pair[0]).strip(), re.sub(r'\[.*?\]', '', pair[1]).strip())))

def evaluate(gt_file_path, predict_file_path):
    with open(gt_file_path, 'r', encoding='utf-8') as gt_file:
        gt_lines = gt_file.readlines()

    with open(predict_file_path, 'r', encoding='utf-8') as predict_file:    
        predicted_pairs = predict_file.readlines()

    # To take only the predicted pairs of the same vocabulary as the GT 
    gt_pairs = extract_gt_pairs(gt_lines)

    # True positive, False positive and False negative sets
    tp = set()
    fp = set()
    fn = set()
    
    for line in predicted_pairs:
        line.strip()
        item1, item2, label = line.split(' || ')
        
        pair = normalize_pair((item1, item2))    
        
        if pair in gt_pairs:
            if int(label) == 1:
                tp.add(pair)
            else:
                fn.add(pair)
        # pair is not gt label 1 and you say it is
        elif int(label) == 1:
            fp.add(pair)
            
    # PRECISION VIENE 1 PERCHE NON ABBIAMO FALSI POSITIVIIIIII :((((((((
    precision = len(tp) / (len(tp) + len(fp)) if (len(tp) + len(fp)) else 0
    recall = len(tp) / (len(tp) + len(fn)) if (len(tp) + len(fn)) else 0
    f1 = 2 * ((precision * recall) / (precision + recall)) if (precision + recall) > 0 else 0

    print(f"\n{CYAN}Pairwise matching EVALUATION for {predict_file_path.rsplit('/', 1)[-1]}{RESET}")
    print(f"- {GREEN}Precision: {RESET}{precision:.2f}")
    print(f"- {GREEN}Recall: {RESET}{recall:.2f}")
    print(f"- {GREEN}F1: {RESET}{f1:.2f}\n")


def get_pairs_for_pairwise_matching(blockin_path: str):
    pairs_to_evaluate = []

    with open(paths.BLOCKING.RESULTS.value + blockin_path, 'r', encoding='utf-8') as blocking_file:
        blocks = json.load(blocking_file)

    for block in blocks:
        if len(block) == 1:
            continue
        pairs_to_evaluate.extend(combinations(block, 2))
    
    return pairs_to_evaluate
        
         
def extract_gt_pairs(gt_lines: list[str]):
    gt_pairs = set()
    
    # GT pairs set
    for line in gt_lines:
        line = line.strip()
        if not line:
            continue

        pair = tuple(line.split(' || '))
        
        gt_pairs.add(pair)
    
    gt_pairs = {normalize_pair(pair) for pair in gt_pairs}
    
    return gt_pairs
