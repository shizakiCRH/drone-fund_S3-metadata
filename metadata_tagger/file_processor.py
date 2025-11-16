"""
File Processor Module

ファイル内容を抽出するモジュール。
PDF、Excel、テキストファイルに対応しています。

サポートされているファイル形式:
- PDF (.pdf): PyMuPDF (fitz) を使用してテキストを抽出
- Excel (.xlsx, .xls): openpyxl を使用してシート内容を読み取り
- テキスト (.txt, .md, .csv等): 直接読み込み
"""

import io
import logging
from typing import Optional

# PDF処理用ライブラリ（Lambda Layerに含まれる）
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None
    logging.warning("PyMuPDF (fitz) is not available. PDF processing will be disabled.")

# Excel処理用ライブラリ（Lambda Layerに含まれる）
try:
    import openpyxl
except ImportError:
    openpyxl = None
    logging.warning("openpyxl is not available. Excel processing will be disabled.")

logger = logging.getLogger()


class FileProcessingError(Exception):
    """ファイル処理エラーの基底クラス"""
    pass


class FileTooLargeError(FileProcessingError):
    """ファイルサイズが大きすぎる場合のエラー"""
    pass


class UnsupportedFileTypeError(FileProcessingError):
    """サポートされていないファイル形式の場合のエラー"""
    pass


def extract_text_from_file(file_key: str, file_content: bytes, max_size: int = 10 * 1024 * 1024) -> str:
    """
    ファイル内容からテキストを抽出する

    Args:
        file_key (str): ファイルのS3キー（拡張子判定に使用）
        file_content (bytes): ファイルのバイナリコンテンツ
        max_size (int): 最大ファイルサイズ（バイト）。デフォルト10MB

    Returns:
        str: 抽出されたテキスト

    Raises:
        FileTooLargeError: ファイルサイズが制限を超えている場合
        FileProcessingError: ファイル処理中にエラーが発生した場合
    """

    # ファイルサイズチェック
    file_size = len(file_content)
    if file_size > max_size:
        raise FileTooLargeError(f"File size ({file_size} bytes) exceeds limit ({max_size} bytes)")

    logger.info(f"Processing file: {file_key} (size: {file_size} bytes)")

    # ファイル拡張子に基づいて処理を分岐
    if file_key.lower().endswith('.pdf'):
        return extract_pdf_text(file_content)
    elif file_key.lower().endswith(('.xlsx', '.xls')):
        return extract_excel_text(file_content)
    else:
        # その他のファイルはテキストとして読み込み
        return extract_text_file(file_content)


def extract_pdf_text(content: bytes) -> str:
    """
    PDFファイルからテキストを抽出する

    Args:
        content (bytes): PDFファイルのバイナリコンテンツ

    Returns:
        str: 抽出されたテキスト

    Raises:
        FileProcessingError: PDF処理中にエラーが発生した場合
    """

    if fitz is None:
        raise FileProcessingError("PyMuPDF is not installed. Cannot process PDF files.")

    try:
        # バイナリコンテンツからPDFドキュメントを開く
        pdf_document = fitz.open(stream=content, filetype="pdf")

        text_parts = []
        total_pages = pdf_document.page_count

        logger.info(f"PDF has {total_pages} pages")

        # 各ページからテキストを抽出（最大3ページまで）
        max_pages = min(total_pages, 3)
        for page_num in range(max_pages):
            page = pdf_document[page_num]
            text = page.get_text()
            text_parts.append(text)

            # デバッグ用: 最初のページのみログ出力
            if page_num == 0:
                logger.info(f"First page text preview: {text[:200]}...")

        pdf_document.close()

        # 全ページのテキストを結合
        full_text = "\n\n".join(text_parts)

        # テキストが空の場合は警告
        if not full_text.strip():
            logger.warning("PDF contains no extractable text")

        return full_text

    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}")
        raise FileProcessingError(f"Failed to extract text from PDF: {str(e)}")


def extract_excel_text(content: bytes) -> str:
    """
    ExcelファイルからテキストをCSV形式で抽出する

    Args:
        content (bytes): Excelファイルのバイナリコンテンツ

    Returns:
        str: 抽出されたテキスト（CSV形式）

    Raises:
        FileProcessingError: Excel処理中にエラーが発生した場合
    """

    if openpyxl is None:
        raise FileProcessingError("openpyxl is not installed. Cannot process Excel files.")

    try:
        # バイナリコンテンツからワークブックを開く
        workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)

        text_parts = []
        sheet_names = workbook.sheetnames

        logger.info(f"Excel has {len(sheet_names)} sheets: {sheet_names}")

        # 各シートを処理（最大1シートのみ - ドキュメント種別判別に十分）
        max_sheets = min(len(sheet_names), 1)
        for sheet_name in sheet_names[:max_sheets]:
            sheet = workbook[sheet_name]

            text_parts.append(f"=== Sheet: {sheet_name} ===")

            # シートの内容を行ごとに読み取り（最大15行まで）
            max_rows = min(sheet.max_row, 15)
            for row_idx, row in enumerate(sheet.iter_rows(max_row=max_rows, values_only=True), start=1):
                # 空行をスキップ
                if all(cell is None or str(cell).strip() == '' for cell in row):
                    continue

                # セルの値をタブ区切りで結合
                row_text = "\t".join([str(cell) if cell is not None else '' for cell in row])
                text_parts.append(row_text)

                # デバッグ用: 最初の5行のみログ出力
                if row_idx <= 5:
                    logger.info(f"Row {row_idx}: {row_text[:100]}...")

        workbook.close()

        # 全シートのテキストを結合
        full_text = "\n".join(text_parts)

        # テキストが空の場合は警告
        if not full_text.strip():
            logger.warning("Excel contains no extractable text")

        return full_text

    except Exception as e:
        logger.error(f"Error extracting text from Excel: {str(e)}")
        raise FileProcessingError(f"Failed to extract text from Excel: {str(e)}")


def extract_text_file(content: bytes) -> str:
    """
    テキストファイルから内容を読み込む

    Args:
        content (bytes): テキストファイルのバイナリコンテンツ

    Returns:
        str: ファイルの内容

    Raises:
        FileProcessingError: テキスト読み込み中にエラーが発生した場合
    """

    try:
        # UTF-8でデコードを試みる
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            # UTF-8で失敗した場合はShift_JISを試す
            logger.info("UTF-8 decoding failed, trying Shift_JIS")
            try:
                text = content.decode('shift_jis')
            except UnicodeDecodeError:
                # Shift_JISでも失敗した場合はCP932を試す
                logger.info("Shift_JIS decoding failed, trying CP932")
                text = content.decode('cp932', errors='replace')

        # デバッグ用: 最初の200文字のみログ出力
        logger.info(f"Text file preview: {text[:200]}...")

        return text

    except Exception as e:
        logger.error(f"Error reading text file: {str(e)}")
        raise FileProcessingError(f"Failed to read text file: {str(e)}")


def truncate_text(text: str, max_length: int = 500) -> str:
    """
    テキストを指定された長さに切り詰める

    OpenAI APIに送信する際のトークン数を制限するために使用します。
    ドキュメント種別の判別には先頭部分で十分なため、先頭のみを取得します。

    Args:
        text (str): 元のテキスト
        max_length (int): 最大文字数。デフォルト500文字

    Returns:
        str: 切り詰められたテキスト
    """

    if len(text) <= max_length:
        return text

    logger.info(f"Truncating text from {len(text)} to {max_length} characters")

    # 先頭部分のみ取得（ドキュメント種別判別には先頭で十分）
    truncated = text[:max_length] + "\n\n... [以降省略]"

    return truncated
