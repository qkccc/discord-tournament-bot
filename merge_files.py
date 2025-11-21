import os

# 1. 設定エリア
# ここにまとめたいファイルの拡張子を指定します
TARGET_EXTENSIONS = ['.py', '.js', '.html', '.css', '.java', '.ts', '.json']

# 無視したいフォルダ名（node_modulesや.gitなど）
IGNORE_DIRS = ['node_modules', '.git', '__pycache__', 'dist', 'build', '.venv']

# 出力するファイル名
OUTPUT_FILENAME = 'project_context.txt'

def merge_files():
    """
    現在のフォルダ以下のファイルを探索し、1つのファイルにまとめます。
    """
    current_dir = os.getcwd()
    
    print(f"処理を開始します: {current_dir}")
    
    try:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as outfile:
            # フォルダ内を歩くように探索（walk）します
            for root, dirs, files in os.walk(current_dir):
                
                # 無視するフォルダを除外する処理
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
                
                for file in files:
                    # 拡張子をチェック
                    _, ext = os.path.splitext(file)
                    if ext in TARGET_EXTENSIONS:
                        file_path = os.path.join(root, file)
                        
                        # 相対パス（プロジェクト内での位置）を取得
                        relative_path = os.path.relpath(file_path, current_dir)
                        
                        print(f"追加中: {relative_path}")
                        
                        # 出力ファイルへの書き込み
                        outfile.write(f"\n{'='*50}\n")
                        outfile.write(f"File: {relative_path}\n")
                        outfile.write(f"{'='*50}\n")
                        
                        try:
                            with open(file_path, 'r', encoding='utf-8') as infile:
                                outfile.write(infile.read())
                                outfile.write("\n") # ファイルの終わりに改行を追加
                        except Exception as e:
                            outfile.write(f"[エラー: ファイルを読み込めませんでした - {e}]\n")

        print(f"\n完了しました！ '{OUTPUT_FILENAME}' が作成されました。")
        print("このファイルをGeminiにアップロードしてください。")

    except Exception as e:
        print(f"予期せぬエラーが発生しました: {e}")

if __name__ == "__main__":
    merge_files()