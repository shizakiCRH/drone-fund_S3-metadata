"""
Slack Notifier Module

エラー発生時にSlackに通知を送信するモジュール。

機能:
- Slack Incoming Webhookを使用してメッセージを送信
- エラー種別、ファイルパス、詳細メッセージを整形して通知
- 通知失敗時もエラーを発生させず、ログに記録のみ行う
"""

import json
import logging
from typing import Dict, Optional
import requests

logger = logging.getLogger()


class SlackNotificationError(Exception):
    """Slack通知エラーの基底クラス"""
    pass


def send_error_notification(
    webhook_url: str,
    error_type: str,
    file_key: str,
    message: str,
    additional_fields: Optional[Dict[str, str]] = None
) -> bool:
    """
    Slackにエラー通知を送信する

    Args:
        webhook_url (str): Slack Incoming WebhookのURL
        error_type (str): エラー種別（例: "file_too_large", "openai_api_error"）
        file_key (str): エラーが発生したファイルのS3キー
        message (str): エラーの詳細メッセージ
        additional_fields (Optional[Dict[str, str]]): 追加で表示したいフィールド

    Returns:
        bool: 通知が成功した場合はTrue、失敗した場合はFalse

    Note:
        この関数は例外を発生させません。通知失敗時はログに記録してFalseを返します。
    """

    if not webhook_url:
        logger.warning("Slack webhook URL is not configured. Skipping notification.")
        return False

    try:
        # Slackメッセージペイロードの構築
        payload = build_slack_payload(error_type, file_key, message, additional_fields)

        logger.info(f"Sending Slack notification for error_type: {error_type}, file: {file_key}")

        # Slack Webhookに送信
        response = requests.post(
            webhook_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10  # 10秒でタイムアウト
        )

        # レスポンスのステータスコードをチェック
        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
            return True
        else:
            logger.error(f"Slack notification failed with status code: {response.status_code}, "
                        f"response: {response.text}")
            return False

    except requests.exceptions.Timeout:
        logger.error("Slack notification timed out")
        return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Slack notification: {str(e)}")
        return False

    except Exception as e:
        logger.error(f"Unexpected error while sending Slack notification: {str(e)}", exc_info=True)
        return False


def build_slack_payload(
    error_type: str,
    file_key: str,
    message: str,
    additional_fields: Optional[Dict[str, str]] = None
) -> Dict:
    """
    Slackメッセージのペイロードを構築する

    Args:
        error_type (str): エラー種別
        file_key (str): ファイルのS3キー
        message (str): エラーメッセージ
        additional_fields (Optional[Dict[str, str]]): 追加フィールド

    Returns:
        Dict: Slackに送信するペイロード
    """

    # エラー種別に応じて色を設定
    color = get_color_for_error_type(error_type)

    # エラー種別の日本語表示名を取得
    error_type_display = get_error_type_display_name(error_type)

    # 基本フィールド
    fields = [
        {
            "title": "エラー種別",
            "value": error_type_display,
            "short": True
        },
        {
            "title": "ファイル",
            "value": file_key,
            "short": False
        },
        {
            "title": "詳細",
            "value": message,
            "short": False
        }
    ]

    # 追加フィールドがあれば追加
    if additional_fields:
        for key, value in additional_fields.items():
            fields.append({
                "title": key,
                "value": value,
                "short": True
            })

    # Slackペイロードの構築
    payload = {
        "text": "⚠️ メタデータタグ付けエラー",
        "attachments": [
            {
                "color": color,
                "fields": fields,
                "footer": "S3 Metadata Tagging System",
                "ts": None  # タイムスタンプは自動で付与される
            }
        ]
    }

    return payload


def get_color_for_error_type(error_type: str) -> str:
    """
    エラー種別に応じた色を返す

    Args:
        error_type (str): エラー種別

    Returns:
        str: 色コード（Slackの色指定）
    """

    color_map = {
        "file_too_large": "warning",      # オレンジ
        "file_read_error": "danger",      # 赤
        "openai_api_error": "danger",     # 赤
        "ai_parse_error": "warning",      # オレンジ
        "metadata_not_found": "#439FE0",  # 青（情報）
        "s3_write_error": "danger",       # 赤
    }

    return color_map.get(error_type, "warning")  # デフォルトはオレンジ


def get_error_type_display_name(error_type: str) -> str:
    """
    エラー種別の日本語表示名を返す

    Args:
        error_type (str): エラー種別

    Returns:
        str: 日本語表示名
    """

    display_names = {
        "file_too_large": "ファイルサイズ超過",
        "file_read_error": "ファイル読み込みエラー",
        "openai_api_error": "OpenAI API エラー",
        "ai_parse_error": "AI応答パースエラー",
        "metadata_not_found": "metadata.json 未検出",
        "s3_write_error": "S3書き込みエラー",
    }

    return display_names.get(error_type, error_type)


def send_success_summary(
    webhook_url: str,
    total_files: int,
    success_count: int,
    error_count: int
) -> bool:
    """
    処理完了時にサマリーをSlackに送信する（オプション機能）

    Args:
        webhook_url (str): Slack Incoming WebhookのURL
        total_files (int): 処理対象ファイル総数
        success_count (int): 成功件数
        error_count (int): エラー件数

    Returns:
        bool: 通知が成功した場合はTrue、失敗した場合はFalse
    """

    if not webhook_url:
        logger.warning("Slack webhook URL is not configured. Skipping summary notification.")
        return False

    try:
        # 成功率を計算
        success_rate = (success_count / total_files * 100) if total_files > 0 else 0

        # 色を決定（エラーが多い場合は警告色）
        if error_count == 0:
            color = "good"  # 緑
        elif error_count < total_files * 0.1:
            color = "warning"  # オレンジ
        else:
            color = "danger"  # 赤

        payload = {
            "text": "✅ メタデータタグ付け処理完了",
            "attachments": [
                {
                    "color": color,
                    "fields": [
                        {
                            "title": "処理ファイル数",
                            "value": str(total_files),
                            "short": True
                        },
                        {
                            "title": "成功",
                            "value": str(success_count),
                            "short": True
                        },
                        {
                            "title": "エラー",
                            "value": str(error_count),
                            "short": True
                        },
                        {
                            "title": "成功率",
                            "value": f"{success_rate:.1f}%",
                            "short": True
                        }
                    ],
                    "footer": "S3 Metadata Tagging System"
                }
            ]
        }

        response = requests.post(
            webhook_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        if response.status_code == 200:
            logger.info("Slack summary notification sent successfully")
            return True
        else:
            logger.error(f"Slack summary notification failed: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"Failed to send Slack summary notification: {str(e)}")
        return False


# テスト用のメイン関数
if __name__ == "__main__":
    # テスト用のWebhook URL（実際の環境では使用しないでください）
    test_webhook_url = "https://hooks.slack.com/services/TEST/TEST/TEST"

    # エラー通知のテスト
    send_error_notification(
        webhook_url=test_webhook_url,
        error_type="file_too_large",
        file_key="会社A/document.pdf",
        message="File size (15MB) exceeds limit (10MB)"
    )

    # サマリー通知のテスト
    send_success_summary(
        webhook_url=test_webhook_url,
        total_files=100,
        success_count=95,
        error_count=5
    )
