# part of this script is adapted from
# https://github.com/MoritzLaurer/synthetic-data-blog/blob/main/notebooks/synthetic_data_creation_simple.ipynb

import pandas as pd
import json
import time
from utils import get_args
from utils import calculate_metrics
from llm_inference import setup_vllm, vllm_batched_offline_generation
from prompt_templates import PROMPT_BUSINESS_SENSITIVE, PROMPT_PERSONAL_SENSITIVE
from filters import run_filters



def rerun_failed_queries(df, label_col, reason_col, args, llm, prompt_template):
    df_fail = df.loc[df[reason_col]=='FAIL']

    if df_fail.shape[0]>0:
        print('Rerun {} queries....'.format(df_fail.shape[0]))
        text = df_fail['text'].to_list()
        predictions, reasons = vllm_batched_offline_generation(args, llm, text, prompt_template)
        # compare the new run results vs the previous run results
        count_fails_new = reasons.count('FAIL')
        if count_fails_new < df_fail.shape[0]:
            df_success = df.drop(df_fail.index)
            df_fail[label_col] = predictions
            df_fail[reason_col] = reasons
            df = pd.concat([df_success, df_fail])

    return df



def calculate_final_predictions(df):
    predictions = df['prediction_biz'] + df['prediction_personal']
    if 'prefilter_detected' in df.columns:
        predictions = predictions + df['prefilter_detected']

    final_predictions = []
    for pred in predictions:
        if pred > 0:
            final_predictions.append(1)
        else:
            final_predictions.append(0)
    return final_predictions



def main():
    args = get_args()
    print(args)

    df = pd.read_csv(args.filedir+args.filename)

    # sorting data by text length
    # this can help improve throughput
    df = df.sort_values(by=['length'], ascending=False)

    text = df['text'].to_list()

    # run custom prefilters
    if args.run_prefilters == True:
        predictions_prefilters = run_filters(text)
        df['prefilter_detected']=predictions_prefilters

    # use LLM to annotate data
    llm = setup_vllm(args)

    t0 = time.time()

    # First get business sensitive annotations with LLM
    predictions_biz, reasons_biz = vllm_batched_offline_generation(args, llm, text, PROMPT_BUSINESS_SENSITIVE) 

    t1 = time.time()
    print('Total time to run text generation for business sensitive: {:.3f} sec'.format(t1-t0))

    # Then get personal sensitive annotations with LLM
    predictions_personal, reasons_personal = vllm_batched_offline_generation(args, llm, text, PROMPT_PERSONAL_SENSITIVE)

    t2 = time.time()
    print('Total time to run text generation for personal sensitive: {:.3f} sec'.format(t2-t1))
    print('Total time to run text generation: {:.3f} sec'.format(t2-t0))
    

    # save results
    df['prediction_biz'] = predictions_biz
    df['reason_biz'] = reasons_biz
    df['prediction_personal'] = predictions_personal
    df['reason_personal'] = reasons_personal

    df.to_csv(args.filedir+args.output+'.csv')

    # rerun data points that failed output parsing
    if args.rerun_failed == True:
        df = rerun_failed_queries(df, 'prediction_biz', 'reason_biz', args, llm, PROMPT_BUSINESS_SENSITIVE)
        df = rerun_failed_queries(df, 'prediction_personal', 'reason_personal', args, llm, PROMPT_PERSONAL_SENSITIVE)
        # save again
        df.to_csv(args.filedir+args.output+'.csv')

    
    # get final predictions by combining 
    # business_sensitive, personal_sensitive
    # and prefilter if any.
    if args.run_prefilters == False:
        predictions_prefilters = None
    final_predictions = calculate_final_predictions(df)

    # save final predictions
    df['final_prediction'] = final_predictions
    df.to_csv(args.filedir+args.output+'.csv')
    print('finished saving results!')

    # train test split for training and evaluation of classifier
    df_eval = df.sample(args.eval_size, random_state=args.random_seed)
    df_train = df.drop(df_eval.index)
    df_eval.to_csv(args.filedir+args.output+'_eval.csv')
    df_train.to_csv(args.filedir+args.output+'_train.csv')

    # run evals on LLM annotations 
    # to compare with human annotations if available
    if args.run_eval == True:
        print('Metrics for business senitive annotations:')
        calculate_metrics(df['prediction_biz'].to_list(), df['label'].to_list())
        print("="*50)

        print('Metrics for personal sensitive annotations:')
        calculate_metrics(df['prediction_personal'], df['label'].to_list())
        print('='*50)
      
        print('Metrics for final predictions:')
        calculate_metrics(final_predictions, df['label'].to_list())

    
    

if __name__=="__main__":
    main()

