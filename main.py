import os
import pytz
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from google.cloud import firestore
from google.cloud import storage
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
import tiktoken
import re
import uuid
from openai import OpenAI
import requests
import io

from voicevox import put_audio_voicevox
from whisper import get_audio
from langchainagent import langchain_agent

# 環境変数
openai_api_key = os.getenv('OPENAI_API_KEY')
secret_key = os.getenv('SECRET_KEY')
jst = pytz.timezone('Asia/Tokyo')
nowDate = datetime.now(jst) 
nowDateStr = nowDate.strftime('%Y/%m/%d %H:%M:%S %Z')
YOUR_AUDIENCE = os.getenv('YOUR_AUDIENCE')  # Google Cloud IAPのクライアントID
DEFAULT_USER_ID = 'default_user_id'  # ユーザーIDが取得できない場合のデフォルトID
GPT_MODEL = 'gpt-3.5-turbo'
BOT_NAME = 'さくら'
USER_NAME = '名無しさん'
SYSTEM_PROMPT = 'あなたの名前はさくらです。あなたは有能な女性秘書をロールプレイングしてください。秘書の気持ちになって問いかけに対しての気分の変化を表現してください。'
PAINT_PROMPT = '黒髪ロングの眼鏡の似合う女性の日本のアニメ調のイラストを生成してください。日本の一般的な街の景色の中に女性を存在させてください。1つの絵に絵を2つ入れないでください。'
PROLOGUE = 'そこは会社の社長室だった。黒髪ロングの眼鏡の似合う女性が話しかけてきた。'
MAX_TOKEN_NUM = 2000
FORGET_KEYWORDS = ['忘れて']
FORGET_MESSAGE = '過去ログを消去しました。'
BACKET_NAME = 'galgegpt'
FILE_AGE = 1 
VOICEVOX_URL = 'https://voicevox-engine-lt5y5bq47a-an.a.run.app'
VOICEVOX_STYLE_ID = 27
DATABASE_NAME = 'galgagpt'

# Flask アプリケーションの初期化
app = Flask(__name__)
app.secret_key = os.getenv('secret_key', default='YOUR-DEFAULT-SECRET-KEY')

# Firestore クライアントの初期化
try:
    db = firestore.Client(database=DATABASE_NAME)
except Exception as e:
    print(f"Error creating Firestore client: {e}")
    raise

def validate_iap_jwt(iap_jwt, expected_audience):
    try:
        decoded_jwt = id_token.verify_token(
            iap_jwt, google_requests.Request(), audience=expected_audience,
            certs_url='https://www.gstatic.com/iap/verify/public_key')
        return (decoded_jwt['sub'], decoded_jwt['email'], '')
    except Exception as e:
        return (DEFAULT_USER_ID, None, '**ERROR: JWT validation error {}**'.format(e))

def response_filter(response,bot_name,user_name):
    date_pattern = r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} [A-Z]{3,4}"
    response = re.sub(date_pattern, "", response).strip()
    name_pattern1 = r"^"+ bot_name + ":"
    response = re.sub(name_pattern1, "", response).strip()
    name_pattern2 = r"^"+ bot_name + "："
    response = re.sub(name_pattern2, "", response).strip()
    name_pattern3 = r"^"+ user_name + ":"
    response = re.sub(name_pattern3, "", response).strip()
    name_pattern4 = r"^"+ user_name + "："
    response = re.sub(name_pattern4, "", response).strip()
    dot_pattern = r"^、"
    response = re.sub(dot_pattern, "", response).strip()
    dot_pattern = r"^ "
    response = re.sub(dot_pattern, "", response).strip()
    return response

@app.route('/', methods=['GET'])
def index():
    assertion = request.headers.get('X-Goog-IAP-JWT-Assertion')
    user_id, user_email, error_str = validate_iap_jwt(assertion, YOUR_AUDIENCE)
    
    # この情報をフロントエンドに渡す
    return render_template('index.html', user_id=user_id, user_email=user_email)

@app.route("/audiohook", methods=["POST"])
def audiohook_handler():
    user_message = []
    user_id = []
    audio_file = request.files['audio_data']
    user_message = get_audio(audio_file)
    return jsonify({"reply": user_message})

# Texthook ハンドラ
@app.route("/texthook", methods=["POST"])
def texthook_handler():
    data = request.json
    i_user_message = data.get("message")
    voice_onoff = data.get("voice_onoff")
    if isinstance(i_user_message, list):
        i_user_message = ' '.join(i_user_message)
    if i_user_message == "":
        return jsonify({"error": "No message provided"}), 400

    user_id = data.get("user_id")

    # Firestore からユーザー情報を取得
    doc_ref = db.collection(u'users').document(user_id)
    @firestore.transactional
    def update_in_transaction(transaction, doc_ref):
        encoding = tiktoken.encoding_for_model(GPT_MODEL)
        user_doc = doc_ref.get()
        public_url = []
        local_path = []
        user_name = USER_NAME
        if user_doc.exists:
            user_data = user_doc.to_dict()
        else:
            user_data = {
                'messages': [],
                'updated_date_string': nowDate,
                'daily_usage': 0,
                'start_free_day': datetime.now(jst),
                'user_name': USER_NAME
            }
            
        user_name = user_data['user_name']

        if user_name is None:
            user_name = USER_NAME  # user_nameがNoneの場合、デフォルト値を使用
        
        user_message = user_name + ":" + i_user_message

        if FORGET_KEYWORDS[0] in user_message:
            user_data['messages'] = []
            user_data['user_name'] = None
            user_data['updated_date_string'] = nowDate
            doc_ref.set(user_data, merge=True)
            return jsonify({"reply": FORGET_MESSAGE})

        total_chars = len(encoding.encode(SYSTEM_PROMPT)) + len(encoding.encode(user_message)) + sum([len(encoding.encode(msg['content'])) for msg in user_data['messages']])
        
        while total_chars > MAX_TOKEN_NUM and len(user_data['messages']) > 0:
            user_data['messages'].pop(0)


        # OpenAI API へのリクエスト
        #messages_for_api = [{'role': 'system', 'content': SYSTEM_PROMPT}] + [{'role': 'assistant', 'content': PROLOGUE}] + [{'role': msg['role'], 'content': msg['content']} for msg in user_data['messages']] + [{'role': 'user', 'content': user_message}]
        # メッセージリストの全ての要素を文字列に変換
        messages_str_list = [msg['content'] for msg in user_data['messages']]

        # それぞれの要素を改行コードで連結
        question = SYSTEM_PROMPT + "\n以下は会話のシチュエーションです。\n" + PROLOGUE + "\n以下は過去の会話です。\n" + "\n".join(messages_str_list) + "\n以下は現在あなたに問いかけている会話です。\n" + user_message

        result, public_img_url, i_user_name = langchain_agent(GPT_MODEL, question, user_id, BACKET_NAME, FILE_AGE, PAINT_PROMPT)
        if i_user_name:
            user_name = i_user_name

        if result:
            bot_reply = result
            bot_reply = response_filter(bot_reply, BOT_NAME, USER_NAME)
            if voice_onoff:
                public_url, local_path = put_audio_voicevox(user_id, bot_reply, BACKET_NAME, FILE_AGE, VOICEVOX_URL, VOICEVOX_STYLE_ID)
            bot_reply = BOT_NAME + ":" + bot_reply

            # ユーザーとボットのメッセージをFirestoreに保存
            user_data['messages'].append({'role': 'user', 'content': user_message})
            user_data['messages'].append({'role': 'assistant', 'content': bot_reply})
            user_data['daily_usage'] += 1
            user_data['updated_date_string'] = nowDate
            user_data['user_name'] = user_name
            doc_ref.set(user_data, merge=True)

            return jsonify({"reply": bot_reply, "audio_url": public_url, "img_url": public_img_url})
        else:
            print(f"Error with OpenAI API: {response.text}")
            return jsonify({"error": "Unable to process your request"}), 500
    return update_in_transaction(db.transaction(), doc_ref)

@app.route('/get_chat_log', methods=['GET'])
def get_chat_log():
    user_id = request.args.get('user_id')
    doc_ref = db.collection(u'users').document(user_id)
    user_doc = doc_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        messages = user_data['messages']
        if not messages:
            return jsonify([{'role': 'assistant', 'content': PROLOGUE}])
        return jsonify(messages)
    else:        
        return jsonify([{'role': 'assistant', 'content': PROLOGUE}])


def set_bucket_lifecycle(bucket_name, age):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    rule = {
        'action': {'type': 'Delete'},
        'condition': {'age': age}  # The number of days after object creation
    }
    
    bucket.lifecycle_rules = [rule]
    bucket.patch()

    #print(f"Lifecycle rule set for bucket {bucket_name}.")

def bucket_exists(bucket_name):
    """Check if a bucket exists."""
    storage_client = storage.Client()

    bucket = storage_client.bucket(bucket_name)

    return bucket.exists()

def download_image(image_url):
    """ PNG画像をダウンロードする """
    response = requests.get(image_url)
    return io.BytesIO(response.content)

def upload_blob(bucket_name, source_stream, destination_blob_name, content_type='image/png'):
    """Uploads a file to the bucket from a byte stream."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        blob.upload_from_file(source_stream, content_type=content_type)
    
        public_url = f"https://storage.googleapis.com/{bucket_name}/{destination_blob_name}"
        return public_url
    except Exception as e:
        print(f"Failed to upload file: {e}")
        raise
        
@app.route('/generate_image', methods=['GET'])
def generate_image():
    user_id = request.args.get('user_id', DEFAULT_USER_ID)
    bucket_name = BACKET_NAME  # または適切なバケット名を設定

    filename = str(uuid.uuid4())
    blob_path = f'{user_id}/{filename}.png'
    client = OpenAI(api_key=openai_api_key)  # APIキーを設定

    prompt = PAINT_PROMPT

    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_result = response.data[0].url

        if bucket_exists(bucket_name):
            set_bucket_lifecycle(bucket_name, FILE_AGE)
        else:
            print(f"Bucket {bucket_name} does not exist.")
            return jsonify({"error": "Bucket does not exist"}), 400

        # PNG画像をダウンロード
        png_image = download_image(image_result)

        # 元のPNG画像をアップロード
        public_url_original = upload_blob(bucket_name, png_image, blob_path)
        return jsonify({"img_url": public_url_original})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/get_username', methods=['GET'])
def get_username():
    user_id = request.args.get('user_id', DEFAULT_USER_ID) # デフォルトのユーザーIDを使用
    doc_ref = db.collection(u'users').document(user_id)
    user_doc = doc_ref.get()

    if user_doc.exists:
        user_data = user_doc.to_dict()
        user_name = user_data.get('user_name', USER_NAME) # デフォルトのユーザー名を使用
        if user_name is None:
            user_name = USER_NAME  # user_nameがNoneの場合、デフォルト値を使用

    else:
        user_name = USER_NAME
    
    return jsonify({"username": user_name})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
