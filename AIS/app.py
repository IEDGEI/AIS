import os
from flask import Flask, request, redirect, render_template, flash, url_for
from google.cloud import storage 
from datetime import datetime
import uuid
import pdfplumber # ⭐️ 1. 파싱 라이브러리 import
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func # ⭐️ PostgreSQL 함수(FTS)를 사용하기 위해 추가

import json # ⭐️ 1. JSON 파싱을 위해 추가
from google.oauth2 import service_account # ⭐️ 2. 서비스 계정 인증을 위해 추가



# ----------------------------------------------------
# 1. Flask 앱 및 DB 설정
# ----------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-very-secret-key'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# ⭐️ GCS 설정
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME") 
ALLOWED_EXTENSIONS = {'pdf'}

# ⭐️ DB 설정: Render에서 제공하는 DATABASE_URL 환경 변수를 읽어옵니다.
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app) # ⭐️ 3. Flask 앱에 DB를 연결

# ----------------------------------------------------
# ⭐️ 4. 데이터베이스 모델(테이블) 정의 ⭐️
# ----------------------------------------------------
class PdfFile(db.Model):
    # 이 구조대로 DB에 테이블이 생성됩니다.
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(500), nullable=False) # 원본 파일명
    gcs_path = db.Column(db.String(1024), unique=True, nullable=False) # GCS 저장 경로
    gcs_url = db.Column(db.String(1024), nullable=False) # GCS 공개 URL
    parsed_text = db.Column(db.Text, nullable=True) # ⭐️ PDF에서 파싱한 텍스트
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<PdfFile {self.original_name}>'

# ----------------------------------------------------
# 5. GCS 및 헬퍼 함수
# ----------------------------------------------------
# app.py의 get_gcs_client 함수를 이 코드로 교체하세요.

def get_gcs_client():
    # 1. 환경 변수에서 JSON 문자열을 읽어옵니다.
    credentials_json_string = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    
    if not credentials_json_string:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS 환경 변수가 설정되지 않았습니다.")
    
    # 2. JSON 문자열을 딕셔너리로 파싱합니다.
    try:
        credentials_info = json.loads(credentials_json_string)
    except json.JSONDecodeError:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS 환경 변수 값(JSON)이 손상되었습니다. Render 대시보드에서 다시 복사/붙여넣기 하세요.")
    
    # 3. 파싱된 딕셔너리 정보로 인증서를 생성합니다.
    credentials = service_account.Credentials.from_service_account_info(credentials_info)
    
    # 4. 인증서를 명시적으로 GCS 클라이언트에 전달합니다.
    return storage.Client(credentials=credentials)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ----------------------------------------------------
# 6. 라우트(Routes) 정의
# ----------------------------------------------------

# ⭐️ [수정됨] index: GCS가 아닌 DB에서 목록을 가져옴
@app.route('/')
def index():
    # 1. HTML 폼에서 'query'라는 이름으로 보낸 검색어를 받습니다.
    search_query = request.args.get('query') 
    
    file_list = []
    
    try:
        # 2. DB에서 기본 쿼리를 준비합니다. (최신순 정렬)
        query_builder = PdfFile.query.order_by(PdfFile.uploaded_at.desc())
        
        # 3. 만약 검색어(search_query)가 있다면, FTS 쿼리를 추가합니다.
        if search_query:
            flash(f"'{search_query}'에 대한 검색 결과입니다.", 'success')
            # ⭐️ PostgreSQL의 전문 검색(FTS) 실행 ⭐️
            # 'english' 언어 기준으로 텍스트를 검색합니다.
            query_builder = query_builder.filter(
                func.to_tsvector('english', PdfFile.parsed_text)
                .match(func.to_tsquery('english', search_query))
            )
            
        # 4. 최종 쿼리를 실행하여 DB에서 파일 목록을 가져옵니다.
        files_from_db = query_builder.all()
        
        # 5. HTML 템플릿에 맞게 데이터 가공 (기존과 동일)
        for file_db in files_from_db:
            if '_' in file_db.original_name:
                display_name = file_db.original_name.split('_', 1)[-1]
            else:
                display_name = file_db.original_name
                
            file_list.append({
                'name': display_name,
                'url': file_db.gcs_url,
                'gcs_path': file_db.gcs_path
            })
            
    except Exception as e:
        flash(f"DB 연결 또는 검색 오류: {e}", "error")
        
    # 6. 검색어를 템플릿으로 다시 보내서, 검색창에 검색어가 남아있도록 합니다.
    return render_template('index.html', files=file_list, search_query=search_query)

# ⭐️ [수정됨] upload: 파싱 기능 추가 및 DB 저장
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'pdfFile' not in request.files:
        flash('파일 부분이 없습니다.', 'error')
        return redirect(url_for('index'))
    
    file = request.files['pdfFile'] 
    
    if file.filename == '':
        flash('선택된 파일이 없습니다.', 'error')
        return redirect(url_for('index'))
    
    if file and allowed_file(file.filename):
        try:
            original_filename = file.filename
            
            # ⭐️ 1. PDF 파싱 수행 (고급 기능) ⭐️
            parsed_text = ""
            file.stream.seek(0) # 스트림을 처음으로 되돌림
            try:
                # pdfplumber로 파일 스트림을 엽니다.
                with pdfplumber.open(file.stream) as pdf:
                    for page in pdf.pages:
                        # 각 페이지의 텍스트를 추출하여 parsed_text 변수에 추가
                        parsed_text += page.extract_text() or "" 
            except Exception as parse_error:
                print(f"파싱 오류 발생 (파일은 저장됨): {parse_error}")
                parsed_text = "파싱 실패"
            
            # ⭐️ 2. GCS에 파일 업로드 ⭐️
            file.stream.seek(0) # GCS 업로드를 위해 다시 스트림 되돌림
            gcs_client = get_gcs_client()
            bucket = gcs_client.bucket(GCS_BUCKET_NAME)
            
            unique_id = uuid.uuid4().hex  
            date_path = datetime.now().strftime('%Y%m%d')
            unique_filename = f"pdf/{date_path}/{unique_id}-{original_filename}"
            
            blob = bucket.blob(unique_filename)
            blob.upload_from_file(file.stream, content_type='application/pdf')
            gcs_file_url = f"https.storage.googleapis.com/{GCS_BUCKET_NAME}/{unique_filename}"

            # ⭐️ 3. DB에 정보 저장 ⭐️
            new_file_entry = PdfFile(
                original_name=original_filename,
                gcs_path=unique_filename,
                gcs_url=gcs_file_url,
                parsed_text=parsed_text # 파싱된 텍스트를 DB에 저장
            )
            db.session.add(new_file_entry)
            db.session.commit()
            
            flash(f'파일 업로드 및 파싱 성공! (GCS 저장됨)', 'success')
            
        except Exception as e:
            db.session.rollback() # 오류 발생 시 DB 롤백
            flash(f'업로드 오류 발생: {e}', 'error')
        
        return redirect(url_for('index'))
            
    else:
        flash('PDF 파일만 업로드 가능합니다.', 'error')
        return redirect(url_for('index'))

# ⭐️ [수정됨] delete: GCS 삭제 및 DB 삭제
@app.route('/delete-files', methods=['POST'])
def delete_files():
    selected_files_paths = request.form.getlist('selected_files')
    
    if not selected_files_paths:
        flash('삭제할 파일을 선택하지 않았습니다.', 'error')
        return redirect(url_for('index'))
        
    delete_count = 0
    
    try:
        gcs_client = get_gcs_client()
        bucket = gcs_client.bucket(GCS_BUCKET_NAME)
        
        for file_path in selected_files_paths:
            # 1. GCS에서 삭제
            blob = bucket.blob(file_path)
            blob.delete()
            
            # 2. DB에서 삭제
            file_to_delete = PdfFile.query.filter_by(gcs_path=file_path).first()
            if file_to_delete:
                db.session.delete(file_to_delete)
            
            delete_count += 1
            
        db.session.commit() # 모든 삭제가 완료된 후 DB 커밋
        flash(f'{delete_count}개의 파일이 GCS 및 DB에서 성공적으로 삭제되었습니다.', 'success')
        
    except Exception as e:
        db.session.rollback() # 오류 발생 시 롤백
        flash(f'파일 삭제 중 오류 발생: {e}', 'error')
        
    return redirect(url_for('index'))

# ----------------------------------------------------
# 7. 서버 실행 (DB 초기화 포함)
# ----------------------------------------------------
if __name__ == '__main__':
    if GCS_BUCKET_NAME is None:
        print("🚨 오류: GCS_BUCKET_NAME 환경 변수를 설정해야 합니다.")
        exit(1)
    if os.environ.get('DATABASE_URL') is None:
        print("🚨 오류: DATABASE_URL 환경 변수를 설정해야 합니다.")
        exit(1)
        
    # ⭐️ 앱 실행 전 DB 테이블 생성 ⭐️
    # PdfFile 모델을 기반으로 DB에 테이블이 없으면 생성합니다.
    with app.app_context():
        db.create_all()
        
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)