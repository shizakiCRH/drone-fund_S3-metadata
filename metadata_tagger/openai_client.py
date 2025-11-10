"""
OpenAI Client Module

OpenAI APIを使用してファイル内容を解析し、
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
    model: str = "gpt-5"
) -> Tuple[Optional[str], Optional[int]]:
    """
    OpenAI APIを使用してファイルからメタデータを抽出する

    Args:
        api_key (str): OpenAI APIキー
        file_path (str): ファイルのS3キー（パス）
        file_content (str): ファイルから抽出されたテキスト内容
        model (str): 使用するOpenAIモデル。デフォルトは "gpt-5"

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
            ]
        )

        # 応答からテキストを取得
        response_text = response.choices[0].message.content
        logger.info(f"OpenAI API response: {response_text}")

        # JSONをパース
        result = parse_ai_response(response_text)

        doc_type = result.get('doc_type')
        doc_date = result.get('doc_date')

        # doc_typeが空の場合は"その他"に設定
        if not doc_type or doc_type.strip() == "":
            doc_type = "その他"

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

1. doc_type: ドキュメントのカテゴリ
   以下のカテゴリから最も適切なものを選択してください:

   - 会社情報: 株主名簿、shareholders register、定款、articles of incorporation、履歴事項、登記簿、registry、謄本、certified copy、規程
   - 営業資料: 営業資料、sales deck、会社案内、company profile、事業紹介、business overview
   - 財務諸表: 決算、financial statements、試算表、trial balance、貸借対照表、balance sheet、損益計算書、profit and loss、P&L、PL、BS、キャッシュフロー、cash flow、FS、TB、税務申告、勘定科目内訳書
   - 株主総会: 株主総会、shareholders meeting、AGM、EGM、種類総会、招集通知、議案、議決、議事録
   - 取締役会: 取締役会、board pack、board minutes、経営会議、executive meeting、議案、議事録
   - 報告資料: 月次、monthly、KPI、report、dashboard、事業報告、株主報告会、株主説明会、定例
   - 事業計画: 事業計画、business plan、利益計画、profit plan、売上計画、sales plan、収支計画、budget、全社戦略、corporate strategy、中期計画、mid term
   - 投資: 投資計画、investment plan、投資委員会、investment committee、募集株式、share issuance、term sheet、shareholder agreement、lock up、round、series、株主間契約、投資契約、株式、株主分配
   - 資本政策: 資本政策、equity strategy、capital policy、エクイティ
   - その他: 上記のいずれにも該当しない場合

   ※ファイルパスや内容から最も適切なカテゴリを判断してください

2. doc_date: ドキュメントの日付（YYYYMMDD形式の数値）
   - 以下の優先順位で日付を探してください:
     1. ファイル名に含まれる日付
     2. ファイルパスの1つ上のディレクトリ名に含まれる日付（例: "2025年4月/"、"2025/04/"、"202504"）
     3. ファイル内容に含まれる最も重要と思われる日付

   - 日付の形式例:
     - "2025年4月17日" → 20250417
     - "2025/04/17" → 20250417
     - "20250417" → 20250417
     - "2025-04-17" → 20250417
     - "2025年4月" → 20250401（日が不明な場合は01日とする）
     - "202504" → 20250401（YYYYMM形式の場合は01日とする）

   - 四半期表記の除外:
     - "2025-2Q", "FY2024 Q3", "2025Q1" などの四半期表記は日付として採用しない
     - これらは無視してください

   - 妥当性検証:
     - 2000年〜2099年の範囲内の日付のみ採用
     - 存在しない日付（例: 20250230、20251332）は採用しない
     - 検証に失敗した場合は null を返す

   - **重要**: 日付に関する明確な文字列が見つからない場合は、無理に日付を推測せず null を返してください

返答は以下のJSON形式でお願いします（例1: 日付が見つかった場合）:
{{
  "doc_type": "会社情報",
  "doc_date": 20250417
}}

返答例2（日付が見つからない場合）:
{{
  "doc_type": "財務諸表",
  "doc_date": null
}}

注意事項:
- doc_typeは上記のカテゴリ名（日本語）をそのまま使用してください
- doc_dateは数値型（YYYYMMDD形式）で返すか、見つからない場合は null（文字列ではなくJSONのnull値）を返してください
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
            result['doc_type'] = "その他"

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
            - doc_typeは必ず文字列を返す（Noneの場合は"その他"）
            - doc_dateはそのまま返す（Noneの可能性あり）
    """

    # doc_typeの検証
    if not doc_type or doc_type.strip() == "":
        logger.info("doc_type is empty, using 'その他'")
        doc_type = "その他"

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
