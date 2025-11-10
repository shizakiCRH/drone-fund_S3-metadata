"""
Lambda Function 1: File Scanner

S3バケット全体を再帰的にスキャンし、.metadata.jsonを除く全ファイルのリストを返却します。

機能:
- S3バケット内の全オブジェクトを取得（ページネーション対応）
- .metadata.jsonファイルを除外
- あらゆる階層の深さのディレクトリ構造に対応
- Step Functionsに渡すファイルリストを生成

入力:
{
    "bucket": "your-bucket-name",
    "prefix": ""  # オプション。特定のプレフィックス配下のみスキャンする場合
}

出力:
{
    "files": [
        {"key": "会社A/document1.pdf", "bucket": "your-bucket-name"},
        {"key": "会社B/report.xlsx", "bucket": "your-bucket-name"}
    ]
}
"""

import boto3
import logging
from typing import Dict, List, Any

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# S3クライアントの初期化
s3_client = boto3.client('s3')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda関数のメインハンドラー

    Args:
        event: Step Functionsからの入力イベント
            - bucket (str): S3バケット名
            - prefix (str): スキャン対象のプレフィックス（オプション、デフォルト: ""）
        context: Lambda実行コンテキスト

    Returns:
        Dict[str, Any]: ファイルリストを含む辞書
            {
                "files": [
                    {"key": "path/to/file.pdf", "bucket": "bucket-name"},
                    ...
                ]
            }

    Raises:
        Exception: S3アクセスエラーやその他の予期しないエラー
    """

    # 入力パラメータの取得
    bucket_name = event.get('bucket')
    prefix = event.get('prefix', '')  # プレフィックスが指定されていない場合は空文字

    logger.info(f"Starting file scan for bucket: {bucket_name}, prefix: '{prefix}'")

    # 入力検証
    if not bucket_name:
        error_msg = "Bucket name is required in the input event"
        logger.error(error_msg)
        raise ValueError(error_msg)

    try:
        # S3バケット内の全ファイルを取得
        files = scan_s3_bucket(bucket_name, prefix)

        logger.info(f"Scan completed. Found {len(files)} files (excluding .metadata.json)")

        return {
            'files': files
        }

    except Exception as e:
        logger.error(f"Error scanning S3 bucket: {str(e)}", exc_info=True)
        raise


def scan_s3_bucket(bucket_name: str, prefix: str = '') -> List[Dict[str, str]]:
    """
    S3バケットを再帰的にスキャンし、.metadata.jsonを除く全ファイルのリストを返す

    この関数は、S3の階層構造の深さに関係なく、すべてのファイルを取得します。
    S3はフラットなキー・バリューストアであるため、list_objects_v2で全オブジェクトを
    一度に取得できます。

    Args:
        bucket_name (str): S3バケット名
        prefix (str): スキャン対象のプレフィックス

    Returns:
        List[Dict[str, str]]: ファイル情報のリスト
            [
                {"key": "path/to/file.pdf", "bucket": "bucket-name"},
                ...
            ]
    """

    files = []
    continuation_token = None

    # ページネーション対応: 全オブジェクトを取得するまでループ
    while True:
        try:
            # list_objects_v2のパラメータを構築
            list_params = {
                'Bucket': bucket_name,
                'Prefix': prefix
            }

            # ContinuationTokenがある場合は追加
            if continuation_token:
                list_params['ContinuationToken'] = continuation_token

            # S3オブジェクトのリストを取得
            response = s3_client.list_objects_v2(**list_params)

            # オブジェクトが存在する場合
            if 'Contents' in response:
                for obj in response['Contents']:
                    key = obj['Key']

                    # .metadata.jsonファイルを除外
                    # ディレクトリ（/で終わるキー）も除外
                    if not key.endswith('.metadata.json') and not key.endswith('/'):
                        files.append({
                            'key': key,
                            'bucket': bucket_name
                        })

                        # デバッグ用: 最初の10ファイルのみログ出力
                        if len(files) <= 10:
                            logger.info(f"Found file: {key}")

            # ページネーション: 次のページがあるかチェック
            if response.get('IsTruncated', False):
                continuation_token = response.get('NextContinuationToken')
                logger.info(f"Fetching next page... (current count: {len(files)})")
            else:
                # 全ページ取得完了
                break

        except s3_client.exceptions.NoSuchBucket:
            error_msg = f"Bucket '{bucket_name}' does not exist"
            logger.error(error_msg)
            raise ValueError(error_msg)

        except Exception as e:
            logger.error(f"Error listing objects in bucket '{bucket_name}': {str(e)}")
            raise

    return files


# ローカルテスト用のメイン関数
if __name__ == "__main__":
    # ローカルテスト用のイベントデータ
    test_event = {
        "bucket": "test-bucket",
        "prefix": ""
    }

    # Lambda関数を実行
    result = lambda_handler(test_event, None)

    print(f"Total files found: {len(result['files'])}")
    print(f"First 5 files: {result['files'][:5]}")
