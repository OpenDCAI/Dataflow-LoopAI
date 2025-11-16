import sys
import sqlite3
from func_timeout import func_timeout, FunctionTimedOut

def execute_sql(data_idx, db_file, sql):
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    try:
        conn.execute("BEGIN TRANSACTION;")
        cursor.execute(sql)
        execution_res = cursor.fetchall()
        execution_res = frozenset(execution_res) # make set hashable
        conn.rollback()
        conn.close()
        return data_idx, db_file, sql, execution_res, 1

        # if len(execution_res) > 0:
        #     return data_idx, db_file, sql, execution_res, 1
        # elif len(execution_res) == 0:
        #     return data_idx, db_file, sql, execution_res, 0
    except:
        conn.rollback()
        conn.close()
        return data_idx, db_file, sql, None, 0

def compare_sql(question_id, db_file, question, ground_truth, pred_sql) :
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    correctness = 0

    try:
        conn.execute("BEGIN TRANSACTION;")
        cursor.execute(pred_sql)
        predicted_res = cursor.fetchall()
        cursor.execute(ground_truth)
        ground_truth_res = cursor.fetchall()
        print('Successfully executed')
        if set(predicted_res) == set(ground_truth_res):
            correctness = 1
        #截取predicted_res的前200个字符保存为字符串
        predicted_res_str = str(predicted_res)[:200]

        conn.rollback()
    except:
        conn.rollback()
    finally:
        conn.close()
    return question_id, db_file, question, ground_truth, pred_sql, correctness, predicted_res_str

def compare_sql_wrapper(args, timeout, completion_id):
    '''Wrap execute_sql for timeout'''
    try:
        result = func_timeout(timeout, compare_sql, args=args)
    except KeyboardInterrupt:
        sys.exit(0)
    except FunctionTimedOut:
        result = (*args, 0, "Timed out")
    except Exception as e:
        result = (*args, 0, f"Error: {e}")
    question_id, db_file, question, ground_truth, pred_sql, correctness, predicted_res_str = result
    return dict(
        task_id=question_id,
        passed=correctness == 1,
        result=predicted_res_str,
        completion_id=completion_id,
    )