# rosbag-browser

rosbagを管理・検索するアプリ

## 機能

- `/bags` でbag一覧を表示
  - ローカル起動時は任意のルートディレクトリを選択
  - Scanで手動インデックス更新
  - bagの破損をチェック（現状はファイルの欠損のみ確認）
  - トピック名、キーワード（bag名、メモ）、タグ、記録開始時刻で検索
- `/bags/{id}` で概要とトピック一覧を表示
  - bagごとのメモ・タグ編集

## ローカル環境で使う

`BAG_ROOT` を設定せずにホストPythonで起動すると、UIから任意のローカルディレクトリを選べます。

実行には[`uv`](https://github.com/astral-sh/uv)が必要です。

```bash
cd /path/to/rosbag-browser
uv sync
uv run rosbag-browser --reload
```

上記実行後、ブラウザで `http://localhost:8000` を開き、`Current bag root` からルートディレクトリを指定してください。
ポートを変更する場合は `uv run rosbag-browser --port 8001 --reload` のように指定できます。

## Dockerで使う

NASなどサーバーで運用する場合は、ルートディレクトリ（既定は `/share/Bags`）を `HOST_BAG_ROOT` で指定して起動します。

```bash
docker compose build rosbag-browser
HOST_BAG_ROOT=/path/to/Bags docker compose up
```

上記実行後、ブラウザで `http://<your-host>:8000` を開いてください。

## テスト

テストは以下のコマンドで実行できます。

```bash
docker compose run --rm rosbag-browser pytest
```
