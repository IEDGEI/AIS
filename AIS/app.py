import os
import json
import uuid
import pdfplumber
from datetime import datetime
from flask import Flask, request, redirect, render_template, flash, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from google.cloud import storage 
from google.oauth2 import service_account

# ----------------------------------------------------
# 1. Flask 앱 및 DB 설정
# ----------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-very-secret-key-change-this' # 이 부분은 나중에 바꿔주세요.
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 # 10MB 업로드 제한

# ⭐️ GCS 설정
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME") 
ALLOWED_EXTENSIONS = {'pdf'}

# ⭐️ DB 설정: Render의 DATABASE_URL 환경 변수를 읽어옵니다.
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app) # Flask 앱에 DB를 연결

# ----------------------------------------------------
# 2. 데이터베이스 모델(테이블) 정의
# ----------------------------------------------------
class PdfFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(500), nullable=False) # 원본 파일명
    gcs_path = db.Column(db.String(1024), unique=True, nullable=False) # GCS 저장 경로
    gcs_url = db.Column(db.String(1024), nullable=False) # GCS 공개 URL
    parsed_text = db.Column(db.Text, nullable=True) # PDF에서 파싱한 텍스트
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<PdfFile {self.original_name}>'

# ----------------------------------------------------
# 3. GCS 및 헬퍼 함수
# ----------------------------------------------------
def get_gcs_client():
    """
    Render 환경 변수에 저장된 JSON 문자열을 파싱하여
    GCS 클라이언트 인증을 완료합니다. (Render 배포용)
    """
    credentials_json_string = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    
    if not credentials_json_string:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS 환경 변수가 설정되지 않았습니다.")
    
    try:
        credentials_info = json.loads(credentials_json_string)
    except json.JSONDecodeError:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS JSON 값이 손상되었습니다. Render 대시보드에서 다시 복사/붙여넣기 하세요.")
    
    credentials = service_account.Credentials.from_service_account_info(credentials_info)
    return storage.Client(credentials=credentials)

def allowed_file(filename):
    """파일 확장자가 'pdf'인지 확인합니다."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ----------------------------------------------------
# 4. 라우트(Routes) 정의
# ----------------------------------------------------

@app.route('/')
def index():
    """
    메인 페이지: 파일 목록을 표시하고 검색 기능을 처리합니다.
    """
    search_query = request.args.get('query') 
    file_list = []
    
    try:
        query_builder = PdfFile.query.order_by(PdfFile.uploaded_at.desc())
        
        if search_query:
            flash(f"'{search_query}'에 대한 검색 결과입니다.", 'success')
            
            # ⭐️ PostgreSQL의 전문 검색(FTS) 실행 ⭐️
            # 'simple' 설정을 사용하여 한글/영문 공백 기준 검색
            query_builder = query_builder.filter(
                func.to_tsvector('simple', PdfFile.parsed_text)
                .match(search_query, postgresql_regconfig='simple')
            )
            
        files_from_db = query_builder.all()
        
        # HTML 템플릿에 맞게 데이터 가공 (접두사 제거)
        for file_db in files_from_db:
            if '_' in file_db.original_name:
                display_name = file_db.original_name.split('_', 1)[-1]
            else:
                display_name = file_db.original_name
                
            file_list.append({
                'name': display_name,
                'url': file_db.gcs_url,
                'gcs_path': file_db.gcs_path # 삭제 시 사용할 고유 경로
            })
            
    except Exception as e:
        flash(f"DB 연결 또는 검색 오류: {e}", "error")
        
    return render_template('index.html', files=file_list, search_query=search_query)


@app.route('/upload', methods=['POST'])
def upload_file():
    """
    파일 업로드 처리:
    1. PDF 텍스트 파싱
    2. GCS에 파일 업로드
    3. DB에 메타데이터 및 텍스트 저장
    """
    if 'pdfFile' not in request.files:
        flash('파일 부분이 없습니다.', 'error')
        return redirect(url_for('index'))
    
    file = request.files['pdfFile'] 
    
    if file.filename == '':
        flash('선택된 파일이 없습니다.', 'error')
        return redirect(url_for('index'))
    
    if file and allowed_file(file.filename):
        try:
            # ⭐️ 한글 파일명 보존 (secure_filename 제거)
            original_filename = file.filename
            
            # 1. PDF 파싱 수행
            parsed_text = ""
            file.stream.seek(0) # 스트림을 처음으로 되돌림
            try:
                with pdfplumber.open(file.stream) as pdf:
                    for page in pdf.pages:
                        parsed_text += page.extract_text() or "" 
            except Exception as parse_error:
                print(f"파싱 오류 (파일은 저장됨): {parse_error}")
                parsed_text = "파싱 실패"
            
            # 2. GCS에 파일 업로드
            file.stream.seek(0) # GCS 업로드를 위해 다시 스트림 되돌림
            gcs_client = get_gcs_client()
            bucket = gcs_client.bucket(GCS_BUCKET_NAME)
            
            unique_id = uuid.uuid4().hex  
            date_path = datetime.now().strftime('%Y%m%d')
            # GCS 경로: pdf/날짜/UUID-원본파일.pdf
            unique_filename = f"pdf/{date_path}/{unique_id}-{original_filename}"
            
            blob = bucket.blob(unique_filename)
            blob.upload_from_file(file.stream, content_type='application/pdf')
            gcs_file_url = f"https.storage.googleapis.com/{GCS_BUCKET_NAME}/{unique_filename}"

            # 3. DB에 정보 저장
            new_file_entry = PdfFile(
                original_name=original_filename,
                gcs_path=unique_filename,
                gcs_url=gcs_file_url,
                parsed_text=parsed_text # 파싱된 텍스트 저장
            )
            db.session.add(new_file_entry)
            db.session.commit()
            
            flash(f'파일 업로드 및 파싱 성공!', 'success')
            
        except Exception as e:
            db.session.rollback() # 오류 발생 시 DB 롤백
            flash(f'업로드 오류 발생: {e}', 'error')
        
        return redirect(url_for('index'))
            
    else:
        flash('PDF 파일만 업로드 가능합니다.', 'error')
        return redirect(url_for('index'))


@app.route('/delete-files', methods=['POST'])
def delete_files():
    """
    파일 삭제 처리:
    1. GCS에서 파일 삭제
    2. DB에서 메타데이터 삭제
    """
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
# 5. 서버 실행 (파일의 맨 마지막에 위치)
# ----------------------------------------------------
if __name__ == '__main__':
    # 필수 환경 변수 확인
    if GCS_BUCKET_NAME is None:
        print("🚨 오류: GCS_BUCKET_NAME 환경 변수를 설정해야 합니다.")
        exit(1)
    if os.environ.get('DATABASE_URL') is None:
        print("🚨 오류: DATABASE_URL 환경 변수를 설정해야 합니다.")
        exit(1)
        
    # 앱 실행 전 DB 테이블 생성
    with app.app_context():
        db.create_all()
        
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)