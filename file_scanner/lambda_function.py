"""
Lambda Function 1: File Scanner (ページネーション対応版)

S3バケットを100件ずつスキャンし、.metadata.jsonを除くファイルのリストを返却します。

機能:
- S3バケット内のオブジェクトを100件ずつ取得（ページネーション対応）
- .metadata.jsonファイルを除外
- あらゆる階層の深さのディレクトリ構造に対応
- Step Functionsでループ処理するための継続トークンを返却

入力:
{
    "bucket": "your-bucket-name",
    "prefix": "",  # オプション。特定のプレフィックス配下のみスキャンする場合
    "continuation_token": None  # 2回目以降はStep Functionsから渡される
}

出力:
{
    "files": [
        "会社A/document1.pdf",
        "会社B/report.xlsx"
    ],
    "continuation_token": "次のページのトークン" or None,
    "is_truncated": true/false,
    "count": 100
}
"""

import boto3
import logging
from typing import Dict, Any

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# S3クライアントの初期化
s3_client = boto3.client('s3')

# 1回のスキャンで取得する最大ファイル数
MAX_KEYS = 750


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda関数のメインハンドラー（ページネーション対応版）

    Args:
        event: Step Functionsからの入力イベント
            - bucket (str): S3バケット名
            - prefix (str): スキャン対象のプレフィックス（オプション、デフォルト: ""）
            - continuation_token (str): 継続トークン（2回目以降のループ用、オプション）
        context: Lambda実行コンテキスト

    Returns:
        Dict[str, Any]: ファイルリストと継続情報を含む辞書
            {
                "files": ["path/to/file.pdf", ...],
                "continuation_token": "next_token" or None,
                "is_truncated": true/false,
                "count": 100
            }

    Raises:
        Exception: S3アクセスエラーやその他の予期しないエラー
    """

    # 入力パラメータの取得
    bucket_name = event.get('bucket')
    prefix = event.get('prefix', '')  # プレフィックスが指定されていない場合は空文字
    continuation_token = event.get('continuation_token')  # 2回目以降のループで使用

    logger.info(f"Starting file scan for bucket: {bucket_name}, prefix: '{prefix}'")
    if continuation_token:
        logger.info(f"Continuation token provided: {continuation_token[:50]}...")  # トークンの最初の50文字のみログ出力
    else:
        logger.info("First scan (no continuation token)")

    # 入力検証
    if not bucket_name:
        error_msg = "Bucket name is required in the input event"
        logger.error(error_msg)
        raise ValueError(error_msg)

    try:
        # S3バケットから100件ずつファイルを取得
        result = scan_s3_bucket_paginated(bucket_name, prefix, continuation_token)

        logger.info(f"Scan completed. Found {result['count']} files in this batch")
        logger.info(f"Is truncated: {result['is_truncated']}")

        return result

    except Exception as e:
        logger.error(f"Error scanning S3 bucket: {str(e)}", exc_info=True)
        raise


def scan_s3_bucket_paginated(
    bucket_name: str, 
    prefix: str = '', 
    continuation_token: str = None
) -> Dict[str, Any]:
    """
    S3バケットを100件ずつスキャンし、.metadata.jsonを除くファイルのリストを返す

    この関数は1回の呼び出しで最大100件のファイルを返し、まだファイルが残っている場合は
    continuation_tokenを返します。Step Functionsがこのトークンを使って次のバッチを要求します。

    Args:
        bucket_name (str): S3バケット名
        prefix (str): スキャン対象のプレフィックス
        continuation_token (str): 継続トークン（前回の実行で取得）

    Returns:
        Dict[str, Any]: ファイルリストと継続情報
            {
                "files": ["path/to/file.pdf", ...],
                "continuation_token": "next_token" or None,
                "is_truncated": true/false,
                "count": 100
            }
    """

    files = []

    try:
        # list_objects_v2のパラメータを構築
        list_params = {
            'Bucket': bucket_name,
            'Prefix': prefix,
            'MaxKeys': MAX_KEYS  # 100件ずつ取得
        }

        # ContinuationTokenがある場合は追加（2回目以降のループ）
        if continuation_token:
            list_params['ContinuationToken'] = continuation_token

        # S3オブジェクトのリストを取得
        logger.info(f"Calling list_objects_v2 with MaxKeys={MAX_KEYS}")
        response = s3_client.list_objects_v2(**list_params)

        # レスポンス情報のログ出力
        key_count = response.get('KeyCount', 0)
        is_truncated = response.get('IsTruncated', False)
        logger.info(f"S3 Response - KeyCount: {key_count}, IsTruncated: {is_truncated}")

        # オブジェクトが存在する場合
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']

                # .metadata.jsonファイルを除外
                if key.endswith('.metadata.json'):
                    logger.debug(f"Skipping metadata file: {key}")
                    continue

                # ディレクトリ（/で終わるキー）を除外
                if key.endswith('/'):
                    logger.debug(f"Skipping directory: {key}")
                    continue

                files.append(key)

                # デバッグ用: 最初の5ファイルのみログ出力
                if len(files) <= 5:
                    logger.info(f"Found file: {key}")

        # 結果を構築
        result = {
            'files': files,
            'continuation_token': response.get('NextContinuationToken'),
            'is_truncated': response.get('IsTruncated', False),
            'count': len(files)
        }

        # サマリーログ
        if result['continuation_token']:
            logger.info(f"More files available. Next continuation token: {result['continuation_token'][:50]}...")
        else:
            logger.info("No more files to scan.")

        return result

    except s3_client.exceptions.NoSuchBucket:
        error_msg = f"Bucket '{bucket_name}' does not exist"
        logger.error(error_msg)
        raise ValueError(error_msg)

    except Exception as e:
        logger.error(f"Error listing objects in bucket '{bucket_name}': {str(e)}")
        raise


# ローカルテスト用のメイン関数
if __name__ == "__main__":
    # ローカルテスト用のイベントデータ（1回目のスキャン）
    test_event_1 = {
        "bucket": "df-metadata-test",
        "prefix": "",
        "continuation_token": None
    }

    print("=== Test 1: First scan ===")
    result_1 = lambda_handler(test_event_1, None)
    print(f"Files found: {result_1['count']}")
    print(f"Is truncated: {result_1['is_truncated']}")
    print(f"First 5 files: {result_1['files'][:5]}")
    
    # 2回目のスキャンをシミュレート（continuation_tokenがある場合）
    if result_1['continuation_token']:
        print("\n=== Test 2: Second scan with continuation token ===")
        test_event_2 = {
            "bucket": "df-metadata-test",
            "prefix": "",
            "continuation_token": result_1['continuation_token']
        }
        result_2 = lambda_handler(test_event_2, None)
        print(f"Files found: {result_2['count']}")
        print(f"Is truncated: {result_2['is_truncated']}")
        print(f"First 5 files: {result_2['files'][:5]}")