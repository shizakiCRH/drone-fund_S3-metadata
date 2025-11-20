#!/bin/bash

###############################################################################
# Lambda Layer ビルドスクリプト (Docker版)
#
# このスクリプトは、Dockerを使用してLambda環境と同じ環境で
# Pythonパッケージをビルドします。これにより、バイナリの互換性問題を回避できます。
#
# 必要な環境:
# - Docker
#
# 使用方法:
#   bash build_layer_docker.sh
#
# 出力:
#   python-dependencies.zip (Lambda Layerとしてアップロード可能なzipファイル)
###############################################################################

set -e  # エラーが発生したら即座に終了

echo "========================================"
echo "Lambda Layer ビルドスクリプト (Docker版)"
echo "========================================"

# Dockerが利用可能かチェック
if ! command -v docker &> /dev/null; then
    echo "エラー: Dockerがインストールされていません"
    echo "Dockerをインストールしてから再度実行してください"
    echo "https://docs.docker.com/get-docker/"
    exit 1
fi

# 作業ディレクトリの設定
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/build"

echo ""
echo "1. 作業ディレクトリのクリーンアップ..."
rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"

echo ""
echo "2. Dockerコンテナ内で依存パッケージをビルド..."
echo "   Lambda Python 3.12環境を使用"

# Lambda Python 3.12と同じ環境でビルド (x86_64アーキテクチャ用)
# イメージが見つからない場合は、最初に以下のコマンドでプルしてください:
# docker pull public.ecr.aws/lambda/python:3.12
docker run --rm \
    --platform linux/amd64 \
    --entrypoint bash \
    -v "${WORK_DIR}:/build" \
    -w /build \
    public.ecr.aws/lambda/python:3.12 \
    -c "
        mkdir -p python && \
        pip install \
            pymupdf \
            openpyxl \
            xlrd \
            python-docx \
            openai \
            requests \
            -t python/ \
            --no-cache-dir \
            --no-compile && \
        find python/ -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
        find python/ -type f -name '*.pyc' -delete 2>/dev/null || true && \
        find python/ -type f -name '*.pyo' -delete 2>/dev/null || true && \
        find python/ -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true && \
        find python/ -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true && \
        find python/ -type f -name '*.so' -exec strip {} + 2>/dev/null || true && \
        find python/ -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true && \
        find python/ -type d -name 'examples' -exec rm -rf {} + 2>/dev/null || true && \
        find python/ -type f -name '*.md' -delete 2>/dev/null || true && \
        find python/ -type f -name '*.txt' ! -name 'LICENSE*' -delete 2>/dev/null || true
    "

echo ""
echo "3. Lambda Layer zipファイルの作成..."
cd "${WORK_DIR}"
zip -r9 "${SCRIPT_DIR}/python-dependencies.zip" python/ -q

echo ""
echo "========================================"
echo "ビルド完了！"
echo "========================================"
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
echo "5. 互換性のあるランタイム: Python 3.12 を選択"
echo "6. [作成] をクリック"
echo ""
echo "または、AWS CLIで以下のコマンドを実行:"
echo ""
echo "  aws lambda publish-layer-version \\"
echo "    --layer-name python-dependencies \\"
echo "    --zip-file fileb://${SCRIPT_DIR}/python-dependencies.zip \\"
echo "    --compatible-runtimes python3.12"
echo ""
