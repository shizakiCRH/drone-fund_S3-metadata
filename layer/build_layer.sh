#!/bin/bash

###############################################################################
# Lambda Layer ビルドスクリプト
#
# このスクリプトは、Lambda関数で使用するPythonパッケージを含む
# Lambda Layerを構築します。
#
# 必要なパッケージ:
# - pymupdf (PyMuPDF): PDF処理用
# - openpyxl: Excel処理用
# - openai: OpenAI API クライアント
# - requests: HTTP リクエスト用（Slack通知）
#
# 使用方法:
#   bash build_layer.sh
#
# 出力:
#   python-dependencies.zip (Lambda Layerとしてアップロード可能なzipファイル)
###############################################################################

set -e  # エラーが発生したら即座に終了

echo "================================"
echo "Lambda Layer ビルドスクリプト"
echo "================================"

# 作業ディレクトリの設定
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/build"
PYTHON_DIR="${WORK_DIR}/python"

echo ""
echo "1. 作業ディレクトリのクリーンアップ..."
rm -rf "${WORK_DIR}"
mkdir -p "${PYTHON_DIR}"

echo ""
echo "2. 依存パッケージのインストール..."
echo "   インストール先: ${PYTHON_DIR}"

# Lambda Layer用のディレクトリ構造は python/ である必要があります
# 参考: https://docs.aws.amazon.com/lambda/latest/dg/configuration-layers.html

pip install \
    pymupdf \
    openpyxl \
    openai \
    requests \
    -t "${PYTHON_DIR}" \
    --upgrade

echo ""
echo "3. 不要なファイルの削除..."
# __pycache__ や .pyc ファイルを削除してサイズを削減
find "${PYTHON_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true
find "${PYTHON_DIR}" -type f -name "*.pyo" -delete 2>/dev/null || true

# テストファイルも削除
find "${PYTHON_DIR}" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DIR}" -type d -name "test" -exec rm -rf {} + 2>/dev/null || true

echo ""
echo "4. Lambda Layer zipファイルの作成..."
cd "${WORK_DIR}"
zip -r9 "${SCRIPT_DIR}/python-dependencies.zip" python/ -q

echo ""
echo "================================"
echo "ビルド完了！"
echo "================================"
echo ""
echo "出力ファイル: ${SCRIPT_DIR}/python-dependencies.zip"

# ファイルサイズを表示
FILE_SIZE=$(du -h "${SCRIPT_DIR}/python-dependencies.zip" | cut -f1)
echo "ファイルサイズ: ${FILE_SIZE}"

echo ""
echo "次のステップ:"
echo "1. AWS Lambda コンソールを開く"
echo "2. [レイヤー] → [レイヤーを作成] をクリック"
echo "3. 名前: python-dependencies"
echo "4. zipファイルをアップロード: ${SCRIPT_DIR}/python-dependencies.zip"
echo "5. 互換性のあるランタイム: Python 3.11 を選択"
echo "6. [作成] をクリック"
echo ""
echo "または、AWS CLIで以下のコマンドを実行:"
echo ""
echo "  aws lambda publish-layer-version \\"
echo "    --layer-name python-dependencies \\"
echo "    --zip-file fileb://${SCRIPT_DIR}/python-dependencies.zip \\"
echo "    --compatible-runtimes python3.11"
echo ""
