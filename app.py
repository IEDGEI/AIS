import os
from flask import Flask, request, redirect, render_template, flash, url_for
from werkzeug.utils import secure_filename

# 1. 업로드 폴더 및 허용 확장자 설정
# (경고: 이 'uploads' 폴더는 Render에서 재시작 시 초기화됩니다!)
UPLOAD_FOLDER = 'uploads' 
ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 # 10MB 제한
app.config['SECRET_KEY'] = 'your-very-secret-key' # flash 메시지를 위한 시크릿 키

# 업로드 폴더가 없으면 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 허용된 파일 확장자인지 확인
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 루트 경로: 'index.html' 템플릿을 보여줌
@app.route('/')
def index():
    return render_template('index.html')

# '/upload' 경로로 파일이 POST될 때 처리
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
        # 파일 이름 보안 처리
        filename = secure_filename(file.filename)
        # 3. 파일 저장 경로 (
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # *** 중요 ***
        # Render에서 이 코드는 파일을 '임시'로만 저장합니다.
        # 서버가 재시작되면 'save_path'에 저장된 파일은 사라집니다.
        # 영구 저장을 위해서는 AWS S3 등을 사용해야 합니다.
        file.save(save_path)
        
        flash(f'파일 업로드 성공! (임시 저장됨: {filename})', 'success')
        return redirect(url_for('index'))
        
    else:
        flash('PDF 파일만 업로드 가능합니다.', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    # Render는 PORT 환경 변수를 사용
    port = int(os.environ.get('PORT', 5000))
    # Render에서 외부 접속을 허용하기 위해 '0.0.0.0' 사용
    app.run(host='0.0.0.0', port=port)