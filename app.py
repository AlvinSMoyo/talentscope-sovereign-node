import os, json, io, pypdf, gc, glob, hashlib, imaplib, email, re
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
SENDER_NAME = os.getenv("SENDER_NAME", "Alvin - TalentScope")

# IMAP CONFIGURATION (Updated for PrivateEmail)
IMAP_SERVER = os.getenv("IMAP_SERVER", "mail.privateemail.com")
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

# ENHANCED SYSTEM PROMPT WITH CONSISTENT SCORING
SYSTEM_PROMPT = """
You are a Senior UK Recruitment Specialist with deep knowledge of the British job market.

CRITICAL SCORING RULES - ENSURE CONSISTENCY:

1. OVERALL SCORE MUST ALIGN WITH SUB-SCORES:
   - Overall score should be CLOSE TO the average of (stat_score + tech_score + team_score) / 3
   - Do NOT give high overall score (82%) with low sub-scores (Stat: 25%, Tech: 35%, Team: 25%)
   - Example GOOD: Overall 82% → Stat: 80%, Tech: 85%, Team: 80%
   - Example BAD: Overall 82% → Stat: 25%, Tech: 35%, Team: 25% ❌
   
2. SCORING BREAKDOWN:
   - stat_score: RTW status, professional registrations (GMC, NMC), DBS, legal compliance
   - tech_score: Technical skills, experience level, domain expertise
   - team_score: Cultural fit, collaboration skills, communication
   
3. IF A CANDIDATE SCORES LOW ON STATUTORY (e.g., no UK RTW), OVERALL SCORE MUST BE LOW TOO
   - No RTW = Maximum overall score 40%
   - Missing critical registrations = Maximum 50%

4. INDUSTRY RELEVANCE:
   - Completely different industry = "dismissed": true
   - Same industry but junior = Low scores but not dismissed

5. TWO-SENTENCE SUMMARY:
   - First: Overall fit and strengths
   - Second: Gap or concern

OUTPUT VALID JSON ONLY:
{
  "candidate_name": "string",
  "score": integer (must align with sub-scores average),
  "stat_score": integer,
  "tech_score": integer,
  "team_score": integer,
  "summary": "Two sentences",
  "rationale": ["point 1", "point 2", "point 3", "point 4"],
  "email": "email@domain.com",
  "email_body": "Warm personalised message",
  "dismissed": boolean,
  "industry": "primary industry"
}
"""

# IMPROVED CAMPAIGN PROMPTS
CAMPAIGN_PROMPT_TEMPLATE = """
Generate UK recruitment marketing content for this role.

JD:
{jd}

Generate THREE pieces:

1. LINKEDIN POST (Professional social media format):
   - Start with an attention-grabbing opening (e.g., "🚀 Exciting opportunity!")
   - 3-4 short paragraphs (not bullet points)
   - Use 4-6 relevant emojis throughout (not excessive)
   - Include hashtags at the END: #UKJobs #Hiring #[Industry] #[Role]
   - Call to action: "Apply now!" or "DM for details"
   - 150-200 words
   - Format like a REAL LinkedIn post (conversational, engaging, professional)

2. JOB BOARD ADVERTISEMENT (Formal structured format):
   - Use **bold headers** (format: **Header Name**)
   - Sections: **Role Summary**, **Key Responsibilities**, **Essential Requirements**, **What We Offer**
   - Bullet points under each section (use - not *)
   - British English
   - 250-350 words
   - Include "Competitive salary" if not specified

3. EQUALITY ACT 2010 COMPLIANCE AUDIT:
   - Brief professional statement (50-75 words)
   - Confirm JD avoids discriminatory language
   - Reference UK Equality Act 2010

Return as JSON: {{"linkedin": "...", "job_boards": "...", "compliance_report": "..."}}
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
- END WITH SIGNATURE BLOCK:

Best regards,

{sender_name}
Recruitment Team
TalentScope UK
{sender_email}
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
    """Generate job description"""
    try:
        data = request.get_json()
        role = data.get('role_title', 'Role')
        
        prompt = f"""Write a professional, UK-compliant job description for: {role}

Requirements:
- British English
- **Bold markdown headers**: **Role Summary**, **Key Responsibilities**, **Essential Requirements**, **Desirable Skills**, **What We Offer**
- Bullet points under each section (use - not *)
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
    """Analyze CVs with intelligent matching"""
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
                    
                    data["ingestion_stats"]["manual"] = data.get("ingestion_stats", {}).get("manual", 0) + 1
                
                files_to_process.append(fpath)
                
        elif mode == 'warehouse':
            files_to_process = glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], '*.pdf'))
            
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
                    
            except Exception as e:
                print(f"ERROR: {e}")
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
    """Sync CVs from IMAP inbox with auto-acknowledgment"""
    try:
        if not IMAP_PASSWORD:
            return jsonify({"error": "IMAP not configured"}), 400
        
        data = load_data()
        new_cvs = 0
        acknowledgments_sent = 0
        
        # Brevo API instance for sending acknowledgments
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )
        
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        mail.select("inbox")
        
        status, messages = mail.search(None, 'UNSEEN')
        
        if status != "OK":
            return jsonify({"error": "IMAP search failed"}), 500
        
        email_ids = messages[0].split()
        
        for email_id in email_ids:
            try:
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                
                if status != "OK":
                    continue
                
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)
                
                sender = email_message.get("From", "unknown@unknown.com")
                
                # Extract clean email address
                sender_email = sender
                if '<' in sender and '>' in sender:
                    sender_email = sender.split('<')[1].split('>')[0].strip()
                
                # Extract sender name
                sender_name = "Applicant"
                if '<' in sender:
                    sender_name = sender.split('<')[0].strip().strip('"')
                
                cv_found = False
                
                for part in email_message.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue
                    
                    filename = part.get_filename()
                    if filename and filename.lower().endswith('.pdf'):
                        file_bytes = part.get_payload(decode=True)
                        f_hash = get_file_hash(file_bytes)
                        
                        if f_hash not in data.get("hashes", {}):
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
                            
                            data["ingestion_stats"]["email"] = data.get("ingestion_stats", {}).get("email", 0) + 1
                            
                            new_cvs += 1
                            cv_found = True
                            
                            print(f"NEW EMAIL CV: {unique_fname} from {sender_email}")
                
                # SEND AUTO-ACKNOWLEDGMENT if CV was found
                if cv_found:
                    try:
                        acknowledgment_message = f"""Dear {sender_name},

Thank you for submitting your application to TalentScope UK. We have successfully received your CV and it will be reviewed by our recruitment team.

Our AI-powered screening system will evaluate your application against our current vacancies, and we will contact you within 5 working days if your profile matches our requirements.

We appreciate your interest in joining our organisation and wish you the best in your career journey.

Best regards,

{SENDER_NAME}
Recruitment Team
TalentScope UK
{SENDER_EMAIL}"""

                        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                            to=[{"email": sender_email, "name": sender_name}],
                            sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
                            subject="Application Received - TalentScope UK",
                            html_content=f"<html><body><p style='white-space: pre-line;'>{acknowledgment_message}</p></body></html>"
                        )
                        
                        api_instance.send_transac_email(send_smtp_email)
                        acknowledgments_sent += 1
                        
                        print(f"AUTO-ACKNOWLEDGMENT SENT to {sender_email}")
                        
                    except Exception as ack_error:
                        print(f"Failed to send acknowledgment to {sender_email}: {ack_error}")
                        # Don't fail the entire sync if acknowledgment fails
                
                # Mark email as read
                mail.store(email_id, '+FLAGS', '\\Seen')
                
            except Exception as e:
                print(f"Email processing error: {e}")
                continue
        
        mail.close()
        mail.logout()
        
        save_data(data)
        
        log_system_event("EMAIL_SYNC", f"Synced {new_cvs} CVs, sent {acknowledgments_sent} acknowledgments")
        
        return jsonify({
            "status": "success",
            "new_cvs": new_cvs,
            "acknowledgments_sent": acknowledgments_sent,
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
    """Get analytics data"""
    try:
        data = load_data()
        candidates = data.get("candidates", [])
        
        industry_dist = {}
        for c in candidates:
            industry = c.get('industry', 'Unknown')
            industry_dist[industry] = industry_dist.get(industry, 0) + 1
        
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
        
        return jsonify(logs[-50:])
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/send_outreach', methods=['POST'])
def send_outreach():
    """Send single email"""
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
    """Bulk decisioning with consistent signatures"""
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
                                rationale="\n".join(candidate.get('rationale', [])),
                                sender_name=SENDER_NAME,
                                sender_email=SENDER_EMAIL
                            )}],
                            timeout=30
                        )
                        message = response.choices[0].message.content.strip()
                    except:
                        message = f"""Dear {name},

Congratulations on being shortlisted!

Best regards,

{SENDER_NAME}
Recruitment Team
TalentScope UK
{SENDER_EMAIL}"""
                    
                    results["preview_messages"][name] = {"type": "shortlist", "message": message}
                else:
                    regret = f"""Dear {name},

Thank you for your application. After careful consideration, we have decided to progress with other candidates whose experience more closely aligns with our requirements.

We wish you success in your career search.

Best regards,

{SENDER_NAME}
Recruitment Team
TalentScope UK"""
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
                                rationale="\n".join(candidate.get('rationale', [])),
                                sender_name=SENDER_NAME,
                                sender_email=SENDER_EMAIL
                            )}],
                            timeout=30
                        )
                        message = response.choices[0].message.content.strip()
                    except:
                        message = f"Dear {name},\n\nCongratulations!\n\nBest regards,\n\n{SENDER_NAME}\nRecruitment Team\nTalentScope UK\n{SENDER_EMAIL}"
                    
                    subject = "Interview Invitation - TalentScope UK"
                else:
                    subject = "Application Update - TalentScope UK"
                    message = f"""Dear {name},

Thank you for your application. After careful consideration, we have decided to progress with other candidates whose experience more closely aligns with our requirements.

We wish you success in your career search.

Best regards,

{SENDER_NAME}
Recruitment Team
TalentScope UK"""
                
                send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                    to=[{"email": email_addr, "name": name}],
                    sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
                    subject=subject,
                    html_content=f"<html><body><p style='white-space: pre-line;'>{message}</p></body></html>"
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
    """Clear all CVs"""
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
    """Retrieve candidate data"""
    try:
        name = request.args.get('name', '').strip()
        
        if not name:
            return jsonify({"error": "No name provided"}), 400
        
        data = load_data()
        
        for candidate in data["candidates"]:
            if candidate.get('candidate_name') == name:
                if 'status' not in candidate:
                    candidate['status'] = 'Applied'
                if 'notes' not in candidate:
                    candidate['notes'] = ''
                return jsonify(candidate)
        
        return jsonify({"error": "Candidate not found"}), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download_cv/<filename>')
def download_cv(filename):
    """Download CV"""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    except:
        return jsonify({"error": "File not found"}), 404

@app.route('/generate_campaign', methods=['POST'])
def generate_campaign():
    """Generate marketing content with improved formatting"""
    try:
        jd = request.json.get('full_jd', '').strip()
        
        if not jd:
            return jsonify({"error": "JD required"}), 400
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "UK recruitment marketing expert. Output valid JSON with properly formatted content."},
                {"role": "user", "content": CAMPAIGN_PROMPT_TEMPLATE.format(jd=jd)}
            ],
            response_format={"type": "json_object"},
            timeout=30
        )
        
        campaign_data = json.loads(response.choices[0].message.content)
        
        # Convert **text** to <strong>text</strong> for job boards
        if 'job_boards' in campaign_data:
            job_boards_html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', campaign_data['job_boards'])
            campaign_data['job_boards_html'] = job_boards_html
        
        log_system_event("CAMPAIGN_GENERATED", "Generated marketing content")
        
        return jsonify(campaign_data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get stats"""
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
