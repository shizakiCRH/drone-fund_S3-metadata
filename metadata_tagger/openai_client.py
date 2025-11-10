"""
OpenAI Client Module

OpenAI API (GPT-5-mini) を使用してファイル内容を解析し、
ドキュメント種別（doc_type）と日付（doc_date）を抽出するモジュール。

機能:
- ファイルパスとテキスト内容からdoc_typeとdoc_dateを抽出
- JSON形式で結果を返却
- エラー時は適切なデフォルト値を設定
"""

import json
import logging
from typing import Dict, Optional, Tuple
from openai import OpenAI

logger = logging.getLogger()


class OpenAIClientError(Exception):
    """OpenAI API呼び出しエラーの基底クラス"""
    pass


class AIParseError(OpenAIClientError):
    """AI応答のパースエラー"""
    pass


def extract_metadata_with_ai(
    api_key: str,
    file_path: str,
    file_content: str,
    model: str = "gpt-5-mini"
) -> Tuple[Optional[str], Optional[int]]:
    """
    OpenAI APIを使用してファイルからメタデータを抽出する

    Args:
        api_key (str): OpenAI APIキー
        file_path (str): ファイルのS3キー（パス）
        file_content (str): ファイルから抽出されたテキスト内容
        model (str): 使用するOpenAIモデル。デフォルトは "gpt-5-mini"

    Returns:
        Tuple[Optional[str], Optional[int]]: (doc_type, doc_date)
            - doc_type: ドキュメント種別（例: "投資", "契約書", "議事録"）
            - doc_date: ドキュメント日付（YYYYMMDD形式の数値、例: 20250417）
            ※ 抽出できない場合はNoneを返す

    Raises:
        OpenAIClientError: API呼び出しに失敗した場合
        AIParseError: APIの応答をパースできない場合
    """

    logger.info(f"Extracting metadata for file: {file_path}")

    try:
        # OpenAIクライアントの初期化
        client = OpenAI(api_key=api_key)

        # プロンプトの構築
        prompt = build_prompt(file_path, file_content)

        logger.info(f"Calling OpenAI API with model: {model}")

        # API呼び出し
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a document analysis expert. Always respond with valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,  # 一貫性のある結果を得るために低めに設定
            max_tokens=150,   # 短い応答で十分
            response_format={"type": "json_object"}  # JSON形式を強制
        )

        # 応答からテキストを取得
        response_text = response.choices[0].message.content
        logger.info(f"OpenAI API response: {response_text}")

        # JSONをパース
        result = parse_ai_response(response_text)

        doc_type = result.get('doc_type')
        doc_date = result.get('doc_date')

        # doc_typeが"unknown"の場合はNoneに変換
        if doc_type == "unknown":
            doc_type = None

        # doc_dateの型チェックと変換
        if doc_date is not None:
            try:
                doc_date = int(doc_date)
            except (ValueError, TypeError):
                logger.warning(f"Invalid doc_date format: {doc_date}, setting to None")
                doc_date = None

        logger.info(f"Extracted metadata - doc_type: {doc_type}, doc_date: {doc_date}")

        return doc_type, doc_date

    except Exception as e:
        logger.error(f"Error calling OpenAI API: {str(e)}", exc_info=True)
        raise OpenAIClientError(f"Failed to extract metadata with AI: {str(e)}")


def build_prompt(file_path: str, file_content: str) -> str:
    """
    OpenAI APIに送信するプロンプトを構築する

    Args:
        file_path (str): ファイルパス
        file_content (str): ファイル内容

    Returns:
        str: 構築されたプロンプト
    """

    # ファイル内容が長すぎる場合は切り詰める（最大8000文字）
    max_content_length = 8000
    if len(file_content) > max_content_length:
        file_content = file_content[:max_content_length] + "\n\n... [以降省略]"

    prompt = f"""あなたはドキュメント分析の専門家です。以下のファイルを分析し、JSONで結果を返してください。

ファイルパス: {file_path}

ファイル内容:
{file_content}

以下の情報を抽出してください:

1. doc_type: ドキュメントの種別
   - 例: 投資、契約書、議事録、報告書、財務諸表、プレゼン資料、など
   - ファイルパスや内容から適切な種別を判断してください
   - 種別が特定できない場合は "unknown" を返してください

2. doc_date: ドキュメントの日付（YYYYMMDD形式の数値）
   - ファイル名またはパス内の日付を優先的に使用してください
   - 例: "2025年4月17日" → 20250417
   - 例: "2025/04/17" → 20250417
   - 例: "20250417" → 20250417
   - ファイル内容から日付を推測する場合は、最も重要と思われる日付を選択してください
   - 日付が特定できない場合は null を返してください

返答は以下のJSON形式でお願いします:
{{
  "doc_type": "種別名",
  "doc_date": 20250417
}}

注意事項:
- doc_dateは必ず数値型（YYYYMMDD形式）で返してください
- 日付が特定できない場合のみ null を使用してください
- 必ずJSONのみを返し、説明文は含めないでください
"""

    return prompt


def parse_ai_response(response_text: str) -> Dict:
    """
    OpenAI APIの応答をパースする

    Args:
        response_text (str): APIからの応答テキスト（JSON文字列）

    Returns:
        Dict: パースされた辞書
            {
                "doc_type": "投資",
                "doc_date": 20250417
            }

    Raises:
        AIParseError: パースに失敗した場合
    """

    try:
        # JSONをパース
        result = json.loads(response_text)

        # 必要なキーが存在するかチェック
        if 'doc_type' not in result:
            logger.warning("doc_type not found in AI response, using default")
            result['doc_type'] = "unknown"

        if 'doc_date' not in result:
            logger.warning("doc_date not found in AI response, using default")
            result['doc_date'] = None

        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {response_text}")
        raise AIParseError(f"Invalid JSON response from AI: {str(e)}")


def validate_metadata(doc_type: Optional[str], doc_date: Optional[int]) -> Tuple[str, Optional[int]]:
    """
    抽出されたメタデータを検証し、必要に応じてデフォルト値を設定する

    Args:
        doc_type (Optional[str]): ドキュメント種別
        doc_date (Optional[int]): ドキュメント日付

    Returns:
        Tuple[str, Optional[int]]: 検証済みの (doc_type, doc_date)
            - doc_typeは必ず文字列を返す（Noneの場合は"unknown"）
            - doc_dateはそのまま返す（Noneの可能性あり）
    """

    # doc_typeの検証
    if not doc_type or doc_type.strip() == "":
        logger.info("doc_type is empty, using 'unknown'")
        doc_type = "unknown"

    # doc_dateの検証（YYYYMMDD形式かチェック）
    if doc_date is not None:
        doc_date_str = str(doc_date)
        if len(doc_date_str) != 8:
            logger.warning(f"Invalid doc_date format: {doc_date} (expected YYYYMMDD), setting to None")
            doc_date = None

    return doc_type, doc_date


# テスト用のメイン関数
if __name__ == "__main__":
    # テスト用のサンプルデータ
    test_api_key = "sk-test-key"
    test_file_path = "会社A/2025年4月/投資契約書.pdf"
    test_file_content = """
    投資契約書

    契約日: 2025年4月17日

    甲（投資家）: 株式会社テスト投資
    乙（被投資企業）: スタートアップ株式会社

    本契約により、甲は乙に対して1億円を投資する。
    """

    try:
        doc_type, doc_date = extract_metadata_with_ai(
            api_key=test_api_key,
            file_path=test_file_path,
            file_content=test_file_content
        )

        print(f"Extracted doc_type: {doc_type}")
        print(f"Extracted doc_date: {doc_date}")

    except Exception as e:
        print(f"Error: {str(e)}")
