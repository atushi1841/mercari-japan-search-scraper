FROM apify/actor-python-playwright:3.11

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# エントリポイントはベースイメージの自動検出 (python -m src) に任せる
