# ROS 2 Bag Browser

QNAP TS-264 などの NAS 上に保存した ROS 2 bag を、`metadata.yaml` の軽量 index で管理・検索する FastAPI アプリです。初期版では ROS 2 環境に依存せず、bag 本体のメッセージは読みません。

## Features

- `/bags` で bag 一覧を表示
- topic 名、message type、keyword、tag、status で検索
- `/bags/{id}` で metadata 概要と topic 一覧を表示
- bag ごとの note/tags 編集
- `/bags` の Scan から手動 index 更新
- `metadata.yaml` と参照ファイルの軽量チェックによる status/error 表示

## Docker Compose

```yaml
services:
  rosbag-browser:
    build: .
    ports:
      - "8000:8000"
    environment:
      BAG_ROOT: /bags
      DB_PATH: /data/rosbag-browser.sqlite3
    volumes:
      - /share/Bags:/bags:ro
      - /share/Container/rosbag-browser:/data
    restart: unless-stopped
```

起動後に `http://<NAS>:8000/bags` を開き、Scan で index を更新して一覧を確認します。

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
BAG_ROOT=/path/to/bags DB_PATH=/tmp/rosbag-browser.sqlite3 uvicorn app.main:app --reload
```

## Test

```bash
pytest
```

## Local Docker Test

ローカルでは `docker-compose.override.yml` により test 依存入りの image target を使います。

```bash
mkdir -p /tmp/rosbag-browser/bags /tmp/rosbag-browser/data
docker compose build rosbag-browser
docker compose run --rm rosbag-browser pytest
```

アプリを起動して画面確認する場合:

```bash
docker compose up
```

`docker-compose.override.yml` では `./app` と `./tests` をコンテナへ bind mount し、`uvicorn --reload` で起動します。通常の Python、HTML、CSS の変更は rebuild なしで反映されます。

依存関係、`Dockerfile`、`pyproject.toml` を変更した場合だけ rebuild します。

```bash
docker compose up --build
```

その後、`http://localhost:8000/bags` を開き、Scan で index を更新して一覧を確認します。

## Notes

- bag 保存ディレクトリは read-only mount 前提です。
- note/tags/index DB のみ `DB_PATH` 側に保存します。
- Scan を繰り返しても既存の note/tags は保持されます。
- 初期版では `ros2 bag info`, `mcap info`, `mcap recover`, `ros2 bag reindex` は使いません。
