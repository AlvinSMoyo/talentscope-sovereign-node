import os, json, io, pypdf, gc, glob, hashlib, imaplib, email
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from datetime import datetime
from pathlib import Path
from email.header import decode_header
from flask import Flask, render_template, request, jsonify, redirect, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/var/www/talentscope/uploaded_cvs'
app.config['SESSION_FILE'] = '/var/www/talentscope/data/session_data.json'
app.config['LOGS_FILE'] = '/var/www/talentscope/data/system_logs.json'

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# BREVO EMAIL CONFIGURATION
configuration = sib_api_v3_sdk.Configuration()
configuration.api_key['api-key'] = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "recruitment@talentscope-pilot.pro")
SENDER_NAME = os.getenv("SENDER_NAME", "TalentScope UK")

# IMAP CONFIGURATION
IMAP_SERVER = "mail.smtp2go.com"  # Brevo uses SMTP2GO for IMAP
IMAP_USER = os.getenv("IMAP_USER", "recruitment@talentscope-pilot.pro")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")

# Create necessary directories
Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
Path('/var/www/talentscope/data').mkdir(parents=True, exist_ok=True)

def get_file_hash(file_content):
    """Generate SHA256 hash for robust duplicate detection"""
    if isinstance(file_content, bytes):
        return hashlib.sha256(file_content).hexdigest()
    return hashlib.sha256(file_content.encode('utf-8')).hexdigest()

def log_system_event(event_type, message, details=None):
    """Log system events for audit trail"""
    try:
        logs = []
        if os.path.exists(app.config['LOGS_FILE']):
            with open(app.config['LOGS_FILE'], 'r', encoding='utf-8') as f:
                logs = json.load(f)
        
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "message": message,
            "details": details or {}
        }
        
        logs.append(log_entry)
        
        # Keep only last 1000 logs
        if len(logs) > 1000:
            logs = logs[-1000:]
        
        with open(app.config['LOGS_FILE'], 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Logging error: {e}")

def load_data():
    """Load session data from JSON file"""
    if os.path.exists(app.config['SESSION_FILE']):
        try:
            with open(app.config['SESSION_FILE'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "hashes" not in data:
                    data["hashes"] = {}
                if "cv_metadata" not in data:
                    data["cv_metadata"] = {}
                if "ingestion_stats" not in data:
                    data["ingestion_stats"] = {"email": 0, "manual": 0}
                # Ensure all candidates have status and notes
                for candidate in data.get("candidates", []):
                    if "status" not in candidate:
                        candidate["status"] = "Applied"
                    if "notes" not in candidate:
                        candidate["notes"] = ""
                return data
        except:
            return {"candidates": [], "hashes": {}, "cv_metadata": {}, "ingestion_stats": {"email": 0, "manual": 0}}
    return {"candidates": [], "hashes": {}, "cv_metadata": {}, "ingestion_stats": {"email": 0, "manual": 0}}

def save_data(data):
    """Save session data to JSON file"""
    with open(app.config['SESSION_FILE'], 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ENHANCED SYSTEM PROMPT
SYSTEM_PROMPT = """
You are a Senior UK Recruitment Specialist with deep knowledge of the British job market across all industries.

CRITICAL INTELLIGENCE RULES:

1. INDUSTRY RELEVANCE & CAREER ALIGNMENT:
   - First, identify the industry/sector of BOTH the JD and the CV
   - If industries are completely unrelated (e.g., Medical CV for IT role, Retail CV for Legal role), set "dismissed": true
   - EXCEPTION: If the CV shows career transition intent OR transferable skills, evaluate it but note the mismatch
   - Example: Junior nurse applying for Senior Consultant role = SAME INDUSTRY, evaluate but note experience gap

2. EXPERIENCE LEVEL MATCHING:
   - Junior applicants for Senior roles: DO NOT dismiss if same industry. Score lower on experience but acknowledge potential
   - Career changers within healthcare/tech: Evaluate transition readiness

3. UK-SPECIFIC REQUIREMENTS:
   - Right to Work (RTW) status is CRITICAL for stat_score
   - Professional registrations (GMC, NMC, SRA, etc.) mandatory for regulated professions
   - DBS checks for healthcare, education, social care roles

4. TWO-SENTENCE SUMMARY RULE:
   - First sentence: Overall fit and key strengths
   - Second sentence: Notable gap, concern, or exceptional quality

5. WARM, PERSONALISED EMAIL (email_body):
   - Reference SPECIFIC experience that matches the role
   - Example: "Your 8 years managing ICU at Royal London Hospital makes you an excellent match for our Clinical Lead position."

6. SCORING REALISM:
   - Most candidates: 50-75%
   - Good matches: 70-85%
   - Exceptional: 85-95%

OUTPUT VALID JSON ONLY:
{
  "candidate_name": "string",
  "score": integer (0-100),
  "stat_score": integer,
  "tech_score": integer,
  "team_score": integer,
  "summary": "Two sentences",
  "rationale": ["point 1", "point 2", "point 3", "point 4"],
  "email": "email@domain.com",
  "email_body": "Warm personalised message",
  "dismissed": boolean,
  "industry": "primary industry/sector"
}
"""

CAMPAIGN_PROMPT_TEMPLATE = """
Generate UK recruitment marketing content.

JD: {jd}

1. LINKEDIN POST (150-200 words, 3-5 emojis, hashtags, call to action)
2. JOB BOARD AD (bold headers, bullets, structured like JD, 250-350 words)
3. EQUALITY ACT 2010 AUDIT (50-75 words compliance statement)

Return JSON: {{"linkedin": "...", "job_boards": "...", "compliance_report": "..."}}
"""

SHORTLIST_EMAIL_TEMPLATE = """
Write a professional UK shortlisting email.

Candidate: {candidate_name}
Strengths: {rationale}

Requirements:
- Congratulate on being shortlisted
- Mention 1-2 specific strengths
- Invite to interview
- Mention next steps (contact within 5 working days)
- British English, 100-150 words
"""

@app.route('/')
def dashboard():
    return redirect('/pipeline')

@app.route('/pipeline')
@app.route('/review')
@app.route('/config')
def tabs():
    view_map = {
        '/pipeline': 'pipeline',
        '/review': 'review',
        '/config': 'config'
    }
    view = view_map.get(request.path, 'pipeline')
    return render_template('index.html', view=view)

@app.route('/generate_jd', methods=['POST'])
def generate_jd():
    """Generate job description using OpenAI"""
    try:
        data = request.get_json()
        role = data.get('role_title', 'Role')
        
        prompt = f"""Write a professional, UK-compliant job description for: {role}

Requirements:
- British English
- **Bold markdown headers**: **Role Summary**, **Key Responsibilities**, **Essential Requirements**, **Desirable Skills**, **What We Offer**
- Bullet points under each section
- UK-specific requirements (RTW, DBS, professional registration if applicable)
- UK Equality Act 2010 compliant
- 300-400 words
"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            timeout=30
        )
        
        log_system_event("JD_GENERATED", f"Generated JD for: {role}")
        return jsonify({"jd_text": response.choices[0].message.content})
        
    except Exception as e:
        log_system_event("ERROR", "JD generation failed", {"error": str(e)})
        return jsonify({"error": str(e)}), 500

@app.route('/analyze_tribunal', methods=['POST'])
def analyze_tribunal():
    """Analyze CVs with intelligent industry matching"""
    try:
        jd = request.form.get('full_jd', '').strip()
        mode = request.form.get('mode', 'new')
        
        if not jd:
            return jsonify({"error": "Job description required"}), 400
        
        data = load_data()
        data["candidates"] = []
        
        files_to_process = []
        
        if mode == 'new':
            uploaded_files = request.files.getlist('files')
            
            if not uploaded_files or len(uploaded_files) == 0:
                return jsonify({"error": "No files uploaded"}), 400
            
            for f in uploaded_files:
                if not f or f.filename == '':
                    continue
                
                file_bytes = f.read()
                f_hash = get_file_hash(file_bytes)
                
                if f_hash in data.get("hashes", {}):
                    fpath = data["hashes"][f_hash]
                    print(f"DUPLICATE: {f.filename}")
                else:
                    fname = secure_filename(f.filename)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    unique_fname = f"{timestamp}_{fname}"
                    fpath = os.path.join(app.config['UPLOAD_FOLDER'], unique_fname)
                    
                    with open(fpath, "wb") as buffer:
                        buffer.write(file_bytes)
                    
                    if "hashes" not in data:
                        data["hashes"] = {}
                    data["hashes"][f_hash] = fpath
                    
                    if "cv_metadata" not in data:
                        data["cv_metadata"] = {}
                    data["cv_metadata"][fpath] = {
                        "original_filename": fname,
                        "upload_date": datetime.now().isoformat(),
                        "hash": f_hash,
                        "source": "manual"
                    }
                    
                    # Update ingestion stats
                    data["ingestion_stats"]["manual"] = data.get("ingestion_stats", {}).get("manual", 0) + 1
                    
                    print(f"NEW CV: {unique_fname}")
                
                files_to_process.append(fpath)
                
        elif mode == 'warehouse':
            files_to_process = glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], '*.pdf'))
            print(f"WAREHOUSE SCAN: {len(files_to_process)} CVs")
            
            if len(files_to_process) == 0:
                return jsonify({"error": "No CVs in warehouse"}), 400

        # Process each CV
        for fpath in files_to_process:
            try:
                reader = pypdf.PdfReader(fpath)
                cv_text = " ".join(
                    page.extract_text() 
                    for page in reader.pages 
                    if page.extract_text()
                )
                
                if not cv_text or len(cv_text.strip()) < 50:
                    continue
                
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"JD:\n{jd}\n\n---\n\nCV:\n{cv_text}"}
                    ],
                    response_format={"type": "json_object"},
                    timeout=60
                )
                
                analysis = json.loads(response.choices[0].message.content)
                
                if not analysis.get('dismissed', False):
                    analysis['cv_filename'] = os.path.basename(fpath)
                    analysis['upload_timestamp'] = datetime.now().isoformat()
                    analysis['status'] = 'Applied'
                    analysis['notes'] = ''
                    data["candidates"].append(analysis)
                    print(f"EVALUATED: {analysis['candidate_name']} - {analysis['score']}%")
                    
            except Exception as e:
                print(f"ERROR: {os.path.basename(fpath)}: {e}")
                continue
        
        save_data(data)
        
        sorted_candidates = sorted(
            data["candidates"], 
            key=lambda x: x.get('score', 0), 
            reverse=True
        )
        
        log_system_event("ANALYSIS_COMPLETE", f"Analysed {len(sorted_candidates)} candidates", {"mode": mode})
        return jsonify(sorted_candidates)
        
    except Exception as e:
        log_system_event("ERROR", "Analysis failed", {"error": str(e)})
        return jsonify({"error": str(e)}), 500

@app.route('/sync_email', methods=['POST'])
def sync_email():
    """Sync CVs from IMAP inbox"""
    try:
        if not IMAP_PASSWORD:
            return jsonify({"error": "IMAP not configured"}), 400
        
        data = load_data()
        new_cvs = 0
        
        # Connect to IMAP
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        mail.select("inbox")
        
        # Search for unread emails
        status, messages = mail.search(None, 'UNSEEN')
        
        if status != "OK":
            return jsonify({"error": "IMAP search failed"}), 500
        
        email_ids = messages[0].split()
        
        for email_id in email_ids:
            try:
                # Fetch email
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                
                if status != "OK":
                    continue
                
                # Parse email
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)
                
                # Extract sender
                sender = email_message.get("From", "unknown@unknown.com")
                
                # Look for PDF attachments
                for part in email_message.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue
                    
                    filename = part.get_filename()
                    if filename and filename.lower().endswith('.pdf'):
                        # Download PDF
                        file_bytes = part.get_payload(decode=True)
                        f_hash = get_file_hash(file_bytes)
                        
                        # Check for duplicate
                        if f_hash not in data.get("hashes", {}):
                            # Save CV
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            safe_filename = secure_filename(filename)
                            unique_fname = f"{timestamp}_email_{safe_filename}"
                            fpath = os.path.join(app.config['UPLOAD_FOLDER'], unique_fname)
                            
                            with open(fpath, 'wb') as f:
                                f.write(file_bytes)
                            
                            if "hashes" not in data:
                                data["hashes"] = {}
                            data["hashes"][f_hash] = fpath
                            
                            if "cv_metadata" not in data:
                                data["cv_metadata"] = {}
                            data["cv_metadata"][fpath] = {
                                "original_filename": filename,
                                "upload_date": datetime.now().isoformat(),
                                "hash": f_hash,
                                "source": "email",
                                "sender": sender
                            }
                            
                            # Update ingestion stats
                            data["ingestion_stats"]["email"] = data.get("ingestion_stats", {}).get("email", 0) + 1
                            
                            new_cvs += 1
                            print(f"EMAIL CV: {unique_fname} from {sender}")
                
                # Mark as read
                mail.store(email_id, '+FLAGS', '\\Seen')
                
            except Exception as e:
                print(f"Email processing error: {e}")
                continue
        
        mail.close()
        mail.logout()
        
        save_data(data)
        
        log_system_event("EMAIL_SYNC", f"Synced {new_cvs} new CVs from email")
        
        return jsonify({
            "status": "success",
            "new_cvs": new_cvs,
            "total_emails_processed": len(email_ids)
        })
        
    except Exception as e:
        log_system_event("ERROR", "Email sync failed", {"error": str(e)})
        return jsonify({"error": str(e)}), 500

@app.route('/update_candidate', methods=['POST'])
def update_candidate():
    """Update candidate status and notes"""
    try:
        payload = request.json
        candidate_name = payload.get('candidate_name')
        status = payload.get('status')
        notes = payload.get('notes')
        
        data = load_data()
        
        for candidate in data["candidates"]:
            if candidate.get('candidate_name') == candidate_name:
                if status:
                    candidate['status'] = status
                if notes is not None:
                    candidate['notes'] = notes
                break
        
        save_data(data)
        
        log_system_event("CANDIDATE_UPDATED", f"Updated {candidate_name}", {"status": status})
        
        return jsonify({"status": "success"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    """Get analytics data for dashboard"""
    try:
        data = load_data()
        candidates = data.get("candidates", [])
        
        # Industry distribution
        industry_dist = {}
        for c in candidates:
            industry = c.get('industry', 'Unknown')
            industry_dist[industry] = industry_dist.get(industry, 0) + 1
        
        # Score distribution
        score_ranges = {
            "0-40": 0,
            "41-60": 0,
            "61-80": 0,
            "81-100": 0
        }
        
        for c in candidates:
            score = c.get('score', 0)
            if score <= 40:
                score_ranges["0-40"] += 1
            elif score <= 60:
                score_ranges["41-60"] += 1
            elif score <= 80:
                score_ranges["61-80"] += 1
            else:
                score_ranges["81-100"] += 1
        
        return jsonify({
            "industry_distribution": industry_dist,
            "score_distribution": score_ranges,
            "total_candidates": len(candidates)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_logs', methods=['GET'])
def get_logs():
    """Get system logs"""
    try:
        if not os.path.exists(app.config['LOGS_FILE']):
            return jsonify([])
        
        with open(app.config['LOGS_FILE'], 'r', encoding='utf-8') as f:
            logs = json.load(f)
        
        # Return last 50 logs
        return jsonify(logs[-50:])
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/send_outreach', methods=['POST'])
def send_outreach():
    """Send single email via Brevo"""
    try:
        payload = request.json
        
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )
        
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": payload['email'], "name": payload.get('candidate_name', '')}],
            sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
            subject=payload.get('subject', "Your Application with TalentScope UK"),
            html_content=f"<html><body><p>{payload['message'].replace(chr(10), '<br>')}</p></body></html>"
        )
        
        api_response = api_instance.send_transac_email(send_smtp_email)
        
        log_system_event("EMAIL_SENT", f"Sent email to {payload['email']}")
        
        return jsonify({"status": "sent", "message_id": str(api_response.message_id)})
        
    except Exception as e:
        log_system_event("ERROR", "Email send failed", {"error": str(e)})
        return jsonify({"error": str(e)}), 500

@app.route('/bulk_decision', methods=['POST'])
def bulk_decision():
    """Bulk decisioning with message preview"""
    try:
        payload = request.json
        threshold = int(payload.get('threshold', 65))
        preview_only = payload.get('preview_only', False)
        
        data = load_data()
        candidates = data.get("candidates", [])
        
        if len(candidates) == 0:
            return jsonify({"error": "No candidates"}), 400
        
        results = {
            "shortlisted": [],
            "regrets": [],
            "errors": [],
            "preview_messages": {}
        }
        
        if preview_only:
            for candidate in candidates[:3]:
                name = candidate.get('candidate_name', 'Candidate')
                score = candidate.get('score', 0)
                
                if score >= threshold:
                    try:
                        response = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[{"role": "user", "content": SHORTLIST_EMAIL_TEMPLATE.format(
                                candidate_name=name,
                                rationale="\n".join(candidate.get('rationale', []))
                            )}],
                            timeout=30
                        )
                        message = response.choices[0].message.content.strip()
                    except:
                        message = candidate.get('email_body', 'Default shortlist')
                    
                    results["preview_messages"][name] = {"type": "shortlist", "message": message}
                else:
                    regret = f"""Dear {name},

Thank you for your application. After careful consideration, we have decided to progress with other candidates whose experience more closely aligns with our requirements.

We wish you success in your career search.

Best regards,
{SENDER_NAME}"""
                    results["preview_messages"][name] = {"type": "regret", "message": regret}
            
            return jsonify(results)
        
        # Actual sending
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )
        
        for candidate in candidates:
            email_addr = candidate.get('email', '').strip()
            name = candidate.get('candidate_name', 'Candidate')
            score = candidate.get('score', 0)
            
            if not email_addr:
                results["errors"].append(f"{name}: No email")
                continue
            
            try:
                if score >= threshold:
                    try:
                        response = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[{"role": "user", "content": SHORTLIST_EMAIL_TEMPLATE.format(
                                candidate_name=name,
                                rationale="\n".join(candidate.get('rationale', []))
                            )}],
                            timeout=30
                        )
                        message = response.choices[0].message.content.strip()
                    except:
                        message = candidate.get('email_body', f"Congratulations {name}!")
                    
                    subject = "Interview Invitation - TalentScope UK"
                else:
                    subject = "Application Update - TalentScope UK"
                    message = f"""Dear {name},

Thank you for your application. After careful consideration, we have decided to progress with other candidates.

Best regards,
{SENDER_NAME}"""
                
                send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                    to=[{"email": email_addr, "name": name}],
                    sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
                    subject=subject,
                    html_content=f"<html><body><p>{message.replace(chr(10), '<br>')}</p></body></html>"
                )
                
                api_instance.send_transac_email(send_smtp_email)
                
                if score >= threshold:
                    results["shortlisted"].append({"name": name, "message": message})
                else:
                    results["regrets"].append({"name": name, "message": message})
                    
            except Exception as e:
                results["errors"].append(f"{name}: {str(e)}")
                continue
        
        log_system_event("BULK_DECISION", f"Sent {len(results['shortlisted'])} shortlist, {len(results['regrets'])} regrets")
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/clear_memory', methods=['POST'])
def clear_memory():
    """Clear all stored CVs"""
    try:
        cv_files = glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], '*.pdf'))
        for f in cv_files:
            os.remove(f)
        
        data = {"candidates": [], "hashes": {}, "cv_metadata": {}, "ingestion_stats": {"email": 0, "manual": 0}}
        save_data(data)
        
        log_system_event("MEMORY_CLEARED", f"Deleted {len(cv_files)} CVs")
        
        return jsonify({"status": "success", "cvs_deleted": len(cv_files)})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_candidate_data')
def get_candidate():
    """Retrieve candidate data by name - FIXED VERSION"""
    try:
        name = request.args.get('name', '').strip()
        
        if not name:
            return jsonify({"error": "No candidate name provided"}), 400
        
        data = load_data()
        
        for candidate in data["candidates"]:
            if candidate.get('candidate_name') == name:
                # Ensure status and notes exist
                if 'status' not in candidate:
                    candidate['status'] = 'Applied'
                if 'notes' not in candidate:
                    candidate['notes'] = ''
                return jsonify(candidate)
        
        return jsonify({"error": "Candidate not found"}), 404
        
    except Exception as e:
        print(f"Error in get_candidate_data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/download_cv/<filename>')
def download_cv(filename):
    """Download CV file"""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    except:
        return jsonify({"error": "File not found"}), 404

@app.route('/generate_campaign', methods=['POST'])
def generate_campaign():
    """Generate marketing content"""
    try:
        jd = request.json.get('full_jd', '').strip()
        
        if not jd:
            return jsonify({"error": "JD required"}), 400
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "UK recruitment marketing expert. Output valid JSON."},
                {"role": "user", "content": CAMPAIGN_PROMPT_TEMPLATE.format(jd=jd)}
            ],
            response_format={"type": "json_object"},
            timeout=30
        )
        
        campaign_data = json.loads(response.choices[0].message.content)
        
        log_system_event("CAMPAIGN_GENERATED", "Generated marketing content")
        
        return jsonify(campaign_data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get system statistics"""
    try:
        data = load_data()
        cv_count = len(glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], '*.pdf')))
        
        ingestion_stats = data.get("ingestion_stats", {"email": 0, "manual": 0})
        
        return jsonify({
            "total_candidates": len(data.get("candidates", [])),
            "total_cvs_stored": cv_count,
            "email_ingestion": ingestion_stats.get("email", 0),
            "manual_ingestion": ingestion_stats.get("manual", 0)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
