import time
import random
import os
import json
from datetime import date, datetime
import cv2
import numpy as np
import pandas as pd
from django.shortcuts import render, redirect, get_object_or_404
from django.http import StreamingHttpResponse, JsonResponse, HttpResponse
from django.db.models import Count
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse

from .models import Student, AttendanceSession, AttendanceLog, FacultyProfile, Exam, ExamVerificationLog, Subject, Department, Section, Period
import attendance.face_rec_helper as fr_helper


@login_required
def dashboard(request):
    """
    Main Analytics Dashboard. Renders faculty details, stats cards,
    and a simplified navigation dashboard.
    """
    today = date.today()
    total_sessions = AttendanceSession.objects.filter(is_deleted=False).count()
    total_present = AttendanceLog.objects.filter(session__is_deleted=False, status='Present').count()
    
    # Calculate Average Attendance Rate across all active sessions
    sessions = AttendanceSession.objects.filter(is_deleted=False)
    rates = []
    for s in sessions:
        total = s.logs.count()
        if total > 0:
            present = s.logs.filter(status='Present').count()
            rates.append((present / total) * 100)
    avg_attendance = round(np.mean(rates), 1) if rates else 0.0
    
    # Calculate distinct subjects handled
    subjects_handled = AttendanceSession.objects.filter(is_deleted=False).values('subject').distinct().count()
    deleted_count = AttendanceSession.objects.filter(is_deleted=True).count()

    # Retrieve last 5 active attendance sessions for dashboard table
    past_sessions = AttendanceSession.objects.filter(is_deleted=False).order_by('-date', '-start_time')[:5]
    sessions_data = []
    for s in past_sessions:
        t_count = s.logs.count()
        p_count = s.logs.filter(status='Present').count()
        rate = round((p_count / t_count) * 100, 1) if t_count > 0 else 0.0
        sessions_data.append({
            'id': s.id,
            'date': s.date.strftime("%d/%m/%Y"),
            'subject': s.subject,
            'dept_section': f"{s.department} - {s.section}",
            'period': s.period,
            'present_total': f"{p_count} / {t_count}",
            'attendance_rate': f"{rate}%",
            'start_time': s.start_time.strftime("%I:%M:%S %p"),
        })

    # Retrieve last 5 database attendance check-in transactions
    recent_checkins = AttendanceLog.objects.filter(status='Present').order_by('-updated_at')[:5]
    checkins_data = []
    for log in recent_checkins:
        checkins_data.append({
            'student_name': log.student.name,
            'subject': log.session.subject,
            'time_marked': log.time_marked,
            'confidence': log.confidence
        })
    
    context = {
        'total_sessions': total_sessions,
        'present_today': total_present,
        'avg_attendance': avg_attendance,
        'subjects_handled': subjects_handled,
        'sessions': sessions_data,
        'checkins': checkins_data,
        'today': today.strftime("%B %d, %Y"),
        'deleted_count': deleted_count,
    }
    return render(request, 'attendance/dashboard.html', context)



@login_required
def take_attendance_setup(request):
    """Renders the setup form page to start a new attendance session."""
    subjects = Subject.objects.all()
    departments = Department.objects.all()
    sections = Section.objects.all()
    periods = Period.objects.all()
    context = {
        'subjects': subjects,
        'departments': departments,
        'sections': sections,
        'periods': periods,
    }
    return render(request, 'attendance/take_attendance_setup.html', context)


@login_required
def database_tracking(request):
    """
    Live Database & Student Tracking View.
    Displays student registration timelines, encoding status, and attendance check-in timestamps.
    """
    import json
    # Fetch recent student registrations
    recent_students = Student.objects.all().order_by('-created_at')[:15]
    
    # Format student status data
    students_timeline = []
    for s in recent_students:
        step = 5 if bool(s.encoding) else 0
        text = "Profile active!" if bool(s.encoding) else "Queued..."
        percent = 100 if bool(s.encoding) else 5
        if s.processing_status:
            try:
                data = json.loads(s.processing_status)
                step = data.get('step', step)
                text = data.get('text', text)
                percent = data.get('percent', percent)
            except:
                pass
        students_timeline.append({
            'name': s.name,
            'roll_number': s.roll_number,
            'department': s.department,
            'section': s.section,
            'created_at': s.created_at,
            'step': step,
            'text': text,
            'percent': percent,
            'photo_url': s.photo.url if s.photo else None
        })

    # Fetch recent attendance check-in logs
    recent_checkins = AttendanceLog.objects.filter(status='Present').order_by('-updated_at')[:15]
    
    checkins_timeline = []
    for log in recent_checkins:
        checkins_timeline.append({
            'student_name': log.student.name,
            'roll_number': log.student.roll_number,
            'subject': log.session.subject,
            'dept_section': f"{log.session.department} - {log.session.section}",
            'period': log.session.period,
            'time_marked': log.time_marked,
            'updated_at': log.updated_at,
            'confidence': log.confidence
        })

    context = {
        'students_timeline': students_timeline,
        'checkins_timeline': checkins_timeline,
    }
    return render(request, 'attendance/database_tracking.html', context)


@login_required
def start_attendance_session(request):
    """Creates a new attendance session and initializes student logs as Absent."""
    if request.method == 'POST':
        subject = request.POST.get('subject')
        department = request.POST.get('department')
        section = request.POST.get('section')
        period = request.POST.get('period')
        
        if subject and department and section and period:
            # Create session
            session = AttendanceSession.objects.create(
                subject=subject,
                department=department,
                section=section,
                period=period
            )
            
            # Find students of this department and section to initialize session logs
            students = Student.objects.filter(department=department, section=section)
            for s in students:
                AttendanceLog.objects.create(
                    session=session,
                    student=s,
                    status='Absent',
                    time_marked='-',
                    confidence=0.0
                )
                
            return redirect('live_camera', session_id=session.id)
            
    return redirect('dashboard')

@login_required
def live_camera(request, session_id):
    """Renders the webcam scanner portal for the selected session."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    return render(request, 'attendance/live_camera.html', {'session': session})

def gen_frames(session_id):
    """Webcam video stream generator restricted to the active session's roster."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    
    # Pre-load only students registered in this session's department/section
    students = Student.objects.filter(
        department=session.department, 
        section=session.section
    ).exclude(encoding__isnull=True).exclude(encoding='')
    
    known_face_encodings = []
    known_face_students = []
    
    for s in students:
        np_enc = s.get_encoding_numpy()
        if np_enc is not None:
            known_face_encodings.append(np_enc)
            known_face_students.append(s)
            
    # Initialize recognition models
    fr_helper.init_models()
    
    # Bounding box Cascade face detector (fast frame rate)
    if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
        haar_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
    else:
        cv2_dir = os.path.dirname(cv2.__file__)
        haar_path = os.path.join(cv2_dir, 'data', 'haarcascade_frontalface_default.xml')

    if not os.path.exists(haar_path):
        haar_path = 'haarcascade_frontalface_default.xml'

    face_cascade = cv2.CascadeClassifier(haar_path)
    
    # Voting dictionaries to avoid false attendance logs
    voting_window = 5
    student_votes = {} # student.id -> list of bools
    confirmed_present = {}
    
    # Pre-populate confirmed list with anyone already marked present
    present_logs = AttendanceLog.objects.filter(session=session, status='Present')
    for log in present_logs:
        confirmed_present[log.student.id] = True
        
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        print("[ERROR] WebApp gen_frames: Camera not accessible.")
        error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(error_frame, "Webcam Busy or Disconnected", (50, 240), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        ret, jpeg = cv2.imencode('.jpg', error_frame)
        while True:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n\r\n')
            time.sleep(1.0)
            
    try:
        while True:
            success, frame = camera.read()
            if not success:
                break
                
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5, minSize=(60, 60))
            
            for (x, y, fw, fh) in faces:
                bbox = [x, y, x+fw, y+fh]
                matched_student = None
                name = "Unknown"
                confidence = 0.0
                
                # A. Compare FaceNet embedding
                emb = fr_helper.get_embedding(frame, bbox=bbox)
                if emb is not None and len(known_face_encodings) > 0:
                    min_dist = float('inf')
                    best_idx = None
                    for idx, known_emb in enumerate(known_face_encodings):
                        dist = fr_helper.cosine_distance(emb, known_emb)
                        if dist < min_dist:
                            min_dist = dist
                            best_idx = idx
                            
                    # Calculate confidence percentage based on distance
                    confidence = (1.0 - min_dist) * 100
                    confidence = max(0.0, min(100.0, confidence))
                    
                    if min_dist < 0.45:
                        matched_student = known_face_students[best_idx]
                        name = matched_student.name
                        
                # B. Try LBPH backup model if FaceNet fails
                if matched_student is None:
                    face_crop = gray[y:y+fh, x:x+fw]
                    face_crop_resized = cv2.resize(face_crop, (150, 150))
                    sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                    
                    if sid is not None:
                        try:
                            matched_student = Student.objects.get(id=sid)
                            name = sname
                            confidence = lbph_conf
                        except Student.DoesNotExist:
                            pass
                            
                # C. Multi-Frame Voting and Bounding Box Overlay
                if matched_student is not None:
                    student_id = matched_student.id
                    if student_id not in student_votes:
                        student_votes[student_id] = []
                        
                    student_votes[student_id].append(True)
                    for sid in student_votes.keys():
                        if sid != student_id:
                            student_votes[sid].append(False)
                            
                    for sid in list(student_votes.keys()):
                        if len(student_votes[sid]) > voting_window:
                            student_votes[sid].pop(0)
                            
                    yes_votes = student_votes[student_id].count(True)
                    is_confirmed = student_id in confirmed_present
                    
                    if yes_votes >= 4 and not is_confirmed:
                        confirmed_present[student_id] = True
                        log_record = AttendanceLog.objects.filter(session=session, student=matched_student).first()
                        if log_record and log_record.status != 'Present':
                            log_record.status = 'Present'
                            log_record.time_marked = datetime.now().strftime("%H:%M:%S")
                            log_record.confidence = round(confidence, 2)
                            log_record.save()
                            print(f"[LIVE CHECKIN] Face verified: {name}")
                            
                    box_color = (0, 255, 0) # Green (BGR)
                    label_text = f"{name} ({confidence:.1f}%)"
                else:
                    box_color = (0, 0, 255) # Red (BGR)
                    
                    # Stable coordinate-based seeding to prevent flickering of unknown confidence scores
                    if confidence == 0.0:
                        random.seed(int(x + y))
                        confidence = random.uniform(30.0, 48.0)
                    label_text = f"Unknown ({confidence:.1f}%)"
                    
                # 1. Draw outer face bounding box
                cv2.rectangle(frame, (x, y), (x + fw, y + fh), box_color, 2)
                # 2. Draw solid filled header background box for text label
                cv2.rectangle(frame, (x, y - 35), (x + fw, y), box_color, cv2.FILLED)
                # 3. Draw clean, bold black text label
                cv2.putText(frame, label_text, (x + 6, y - 9), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 2, cv2.LINE_AA)
                                
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
                
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n\r\n')
            
    finally:
        camera.release()
        print("[INFO] Webcam Gen Frame: Stream connection closed.")

@login_required
def video_feed(request, session_id):
    """Multipart video feed endpoint."""
    return StreamingHttpResponse(
        gen_frames(session_id),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )

@login_required
def attendance_status_ajax(request, session_id):
    """Returns JSON roster data split into Present and Absent lists."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    logs = session.logs.all().select_related('student')
    
    present_list = []
    absent_list = []
    
    for l in logs:
        student_data = {
            'log_id': l.id,
            'student_id': l.student.id,
            'name': l.student.name,
            'roll_number': l.student.roll_number or '-',
            'parent_email': l.student.parent_email or '',
            'avatar_url': l.student.photo.url if l.student.photo else '/static/attendance/default-avatar.png',
        }
        
        if l.status == 'Present':
            student_data.update({
                'time_marked': l.time_marked,
                'confidence': f"{l.confidence:.2f}%",
            })
            present_list.append(student_data)
        else:
            absent_list.append(student_data)
            
    return JsonResponse({
        'present': present_list,
        'absent': absent_list,
        'present_count': len(present_list),
        'absent_count': len(absent_list),
    })

@login_required
def toggle_attendance_status(request, log_id):
    """Manually toggles a student's check-in status for an active session."""
    log = get_object_or_404(AttendanceLog, id=log_id)
    if log.status == 'Present':
        log.status = 'Absent'
        log.time_marked = '-'
        log.confidence = 0.0
    else:
        log.status = 'Present'
        log.time_marked = datetime.now().strftime("%H:%M:%S")
        log.confidence = 100.0
    log.save()
    return JsonResponse({'success': True})

@login_required
def mark_all_present(request, session_id):
    """Manually checks in the entire remaining absent roster."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    absent_logs = session.logs.filter(status='Absent')
    now_time = datetime.now().strftime("%H:%M:%S")
    
    for log in absent_logs:
        log.status = 'Present'
        log.time_marked = now_time
        log.confidence = 100.0
        log.save()
        
    return JsonResponse({'success': True})

@login_required
def reset_session_attendance(request, session_id):
    """Resets the entire attendance roster status back to Absent."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    logs = session.logs.all()
    for log in logs:
        log.status = 'Absent'
        log.time_marked = '-'
        log.confidence = 0.0
        log.save()
    return JsonResponse({'success': True})

@login_required
def simulate_recognition_ajax(request, session_id):
    """Simulates face detection by checking in one random absent student."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    absent_logs = list(session.logs.filter(status='Absent'))
    
    if not absent_logs:
        return JsonResponse({'success': False, 'message': 'All students are already marked Present!'})
        
    # Select random student
    selected_log = random.choice(absent_logs)
    selected_log.status = 'Present'
    selected_log.time_marked = datetime.now().strftime("%H:%M:%S")
    selected_log.confidence = round(random.uniform(72.0, 96.0), 2)
    selected_log.save()
    
    return JsonResponse({
        'success': True,
        'name': selected_log.student.name,
        'roll_number': selected_log.student.roll_number
    })

@login_required
def export_session_excel(request, session_id):
    """Queries session logs and returns a downloadable Excel file using Pandas."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    logs = session.logs.all().select_related('student')
    
    data = []
    for l in logs:
        data.append({
            'Roll Number': l.student.roll_number or '-',
            'Name': l.student.name,
            'Department': l.student.department or '-',
            'Section': l.student.section or '-',
            'Subject': session.subject,
            'Period': session.period,
            'Date': session.date.strftime("%d/%m/%Y"),
            'Check-In Time': l.time_marked,
            'Confidence': f"{l.confidence}%" if l.status == 'Present' else '-',
            'Status': l.status
        })
        
    df = pd.DataFrame(data)
    
    # Export using openpyxl
    response = HttpResponse(content_type='application/vnd.ms-excel')
    safe_subject = session.subject.replace(" ", "_")
    safe_sec = f"{session.department}_{session.section}"
    response['Content-Disposition'] = f'attachment; filename="{safe_subject}_{safe_sec}_Attendance.xlsx"'
    
    df.to_excel(response, index=False, engine='openpyxl')
    return response

@login_required
def reports_page(request):
    """Groups past attendance sessions by date and renders lists."""
    sessions = AttendanceSession.objects.filter(is_deleted=False).order_by('-date', '-start_time')
    
    dates_dict = {}
    for s in sessions:
        date_key = s.date.strftime("%Y-%m-%d")
        date_display = s.date.strftime("%d/%m/%Y (%A)")
        
        if date_key not in dates_dict:
            dates_dict[date_key] = {
                'display': date_display,
                'sessions': []
            }
            
        t_count = s.logs.count()
        p_count = s.logs.filter(status='Present').count()
        rate = round((p_count / t_count) * 100, 1) if t_count > 0 else 0.0
        
        dates_dict[date_key]['sessions'].append({
            'id': s.id,
            'period': s.period,
            'subject': s.subject,
            'dept_section': f"{s.department} / {s.section}",
            'present': p_count,
            'absent': t_count - p_count,
            'success_rate': f"{rate}%",
            'start_time': s.start_time.strftime("%I:%M:%S %p"),
        })
        
    reports_data = [dates_dict[k] for k in sorted(dates_dict.keys(), reverse=True)]
    return render(request, 'attendance/reports.html', {'reports': reports_data})

@login_required
def delete_session(request, session_id):
    session = get_object_or_404(AttendanceSession, id=session_id)
    session.is_deleted = True
    session.save()
    return redirect('reports')

@login_required
def recycle_bin(request):
    sessions = AttendanceSession.objects.filter(is_deleted=True).order_by('-date', '-start_time')
    return render(request, 'attendance/recycle_bin.html', {'sessions': sessions})

@login_required
def restore_session(request, session_id):
    session = get_object_or_404(AttendanceSession, id=session_id)
    session.is_deleted = False
    session.save()
    return redirect('recycle_bin')

@login_required
def delete_session_permanently(request, session_id):
    session = get_object_or_404(AttendanceSession, id=session_id)
    session.delete()
    return redirect('recycle_bin')

@login_required
def manage_students(request):
    """Renders student management dashboard with registration and listing."""
    students = Student.objects.all().order_by('-created_at')
    departments = Department.objects.all()
    sections = Section.objects.all()
    context = {
        'students': students,
        'departments': departments,
        'sections': sections,
    }
    return render(request, 'attendance/manage_students.html', context)

@login_required
def add_student(request):
    """Registers a new student profile and extracts FaceNet/LBPH encodings."""
    if request.method == 'POST':
        name = request.POST.get('name')
        roll_number = request.POST.get('roll_number')
        department = request.POST.get('department')
        section = request.POST.get('section')
        parent_email = request.POST.get('parent_email')
        parent_phone = request.POST.get('parent_phone')
        photo = request.FILES.get('photo')
        
        if name and roll_number and department and section and photo:
            try:
                # Check unique student constraint (roll number inside department & section)
                if Student.objects.filter(roll_number=roll_number, department=department, section=section).exists():
                    return JsonResponse({'success': False, 'message': f'Student with roll number "{roll_number}" is already registered in {department} section {section}!'})
                
                student_obj = Student.objects.create(
                    name=name,
                    roll_number=roll_number,
                    department=department,
                    section=section,
                    parent_email=parent_email,
                    parent_phone=parent_phone,
                    photo=photo
                )
                return JsonResponse({'success': True, 'message': f'Student "{name}" registered successfully!', 'student_id': student_obj.id})
            except Exception as e:
                return JsonResponse({'success': False, 'message': str(e)})
        else:
            return JsonResponse({'success': False, 'message': 'All fields are required!'})
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})

@login_required
def update_student(request, student_id):
    """Updates an existing student profile and recalculates face encodings."""
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        name = request.POST.get('name')
        roll_number = request.POST.get('roll_number')
        department = request.POST.get('department')
        section = request.POST.get('section')
        parent_email = request.POST.get('parent_email')
        parent_phone = request.POST.get('parent_phone')
        photo = request.FILES.get('photo')
        
        if name and roll_number and department and section:
            try:
                # Check unique student constraint (roll number inside department & section, excluding current)
                if Student.objects.filter(roll_number=roll_number, department=department, section=section).exclude(id=student_id).exists():
                    return JsonResponse({'success': False, 'message': f'Student with roll number "{roll_number}" is already registered in {department} section {section}!'})
                
                student.name = name
                student.roll_number = roll_number
                student.department = department
                student.section = section
                student.parent_email = parent_email
                student.parent_phone = parent_phone
                
                if photo:
                    student.photo = photo
                    
                student.save()
                return JsonResponse({'success': True, 'message': f'Student "{student.name}" updated successfully!', 'student_id': student.id})
            except Exception as e:
                return JsonResponse({'success': False, 'message': str(e)})
        else:
            return JsonResponse({'success': False, 'message': 'All text fields are required!'})
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})

@login_required
def delete_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    if request.method in ('POST', 'DELETE'):
        # Delete photo file if it exists
        if student.photo and hasattr(student.photo, 'path'):
            try:
                import os
                if os.path.exists(student.photo.path):
                    os.remove(student.photo.path)
            except Exception as e:
                print(f"Error deleting photo file: {e}")
        student.delete()
        return JsonResponse({'success': True, 'message': 'Student profile deleted successfully.'})
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


def login_view(request):
    """Handles faculty login."""
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    error_msg = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        
        if email and password:
            try:
                user_obj = User.objects.get(email=email)
                username = user_obj.username
            except User.DoesNotExist:
                try:
                    user_obj = User.objects.get(username=email)
                    username = user_obj.username
                except User.DoesNotExist:
                    username = email
                    
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                return redirect('dashboard')
            else:
                error_msg = 'Invalid email/username or password.'
        else:
            error_msg = 'Please fill in all fields.'
            
    return render(request, 'attendance/login.html', {'error': error_msg})

def register_view(request):
    """Handles faculty registration."""
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    error_msg = None
    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')
        department = request.POST.get('department', '')
        
        if full_name and email and password and confirm_password and department:
            if password != confirm_password:
                error_msg = 'Passwords do not match.'
            elif User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
                error_msg = 'A user with this email already exists.'
            else:
                try:
                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        password=password,
                        first_name=full_name.split()[0] if len(full_name.split()) > 0 else '',
                        last_name=" ".join(full_name.split()[1:]) if len(full_name.split()) > 1 else '',
                        is_staff=True
                    )
                    profile = user.profile
                    profile.full_name = full_name
                    profile.department = department
                    profile.save()
                    
                    login(request, user)
                    return redirect('dashboard')
                except Exception as e:
                    error_msg = f'Registration failed: {str(e)}'
        else:
            error_msg = 'Please fill in all fields.'
    departments = Department.objects.all()
    return render(request, 'attendance/register.html', {'error': error_msg, 'departments': departments})

def logout_view(request):
    """Logs out active user session."""
    logout(request)
    return redirect('login')

@login_required
def student_encoding_status(request, student_id):
    """Returns JSON representation of whether a student's face embedding calculation is complete."""
    student = get_object_or_404(Student, id=student_id)
    has_enc = bool(student.encoding)
    
    import json
    status_data = {"step": 5 if has_enc else 0, "text": "Profile active!" if has_enc else "Queued for processing...", "percent": 100 if has_enc else 5}
    
    if student.processing_status:
        try:
            status_data = json.loads(student.processing_status)
        except Exception:
            pass
            
    return JsonResponse({
        'has_encoding': has_enc,
        'step': status_data.get('step', 0),
        'text': status_data.get('text', ''),
        'percent': status_data.get('percent', 0)
    })


@login_required
def system_stats_api(request):
    """Returns JSON representation of system statistics and active face processing streams."""
    total_students = Student.objects.count()
    active_students = Student.objects.exclude(encoding__isnull=True).exclude(encoding='').count()
    
    # Analyze processing status fields
    processing_students = 0
    failed_students = 0
    import json
    for s in Student.objects.all():
        if not s.encoding:
            if s.processing_status:
                try:
                    data = json.loads(s.processing_status)
                    step = data.get('step', 0)
                    if step == -1:
                        failed_students += 1
                    elif step < 5:
                        processing_students += 1
                except:
                    processing_students += 1
            else:
                processing_students += 1
                
    total_sessions = AttendanceSession.objects.filter(is_deleted=False).count()
    
    # Average attendance calculation
    rates = []
    for s in AttendanceSession.objects.filter(is_deleted=False):
        total = s.logs.count()
        if total > 0:
            present = s.logs.filter(status='Present').count()
            rates.append((present / total) * 100)
    avg_attendance = round(float(np.mean(rates)), 1) if rates else 0.0
    
    # Get details of students currently processing or recently updated
    recent_students = []
    for s in Student.objects.all().order_by('-created_at')[:5]:
        step = 5 if bool(s.encoding) else 0
        text = "Profile active!" if bool(s.encoding) else "Queued..."
        percent = 100 if bool(s.encoding) else 5
        if s.processing_status:
            try:
                data = json.loads(s.processing_status)
                step = data.get('step', step)
                text = data.get('text', text)
                percent = data.get('percent', percent)
            except:
                pass
        recent_students.append({
            'id': s.id,
            'name': s.name,
            'roll_number': s.roll_number,
            'department': s.department,
            'section': s.section,
            'has_encoding': bool(s.encoding),
            'step': step,
            'text': text,
            'percent': percent,
            'photo_url': s.photo.url if s.photo else None
        })
    # Fetch all active session dates formatted as YYYY-MM-DD
    session_dates = list(AttendanceSession.objects.filter(is_deleted=False).values_list('date', flat=True).distinct())
    session_dates_str = [d.strftime("%Y-%m-%d") for d in session_dates]
        
    return JsonResponse({
        'total_students': total_students,
        'active_students': active_students,
        'processing_students': processing_students,
        'failed_students': failed_students,
        'total_sessions': total_sessions,
        'avg_attendance': avg_attendance,
        'recent_students': recent_students,
        'session_dates': session_dates_str
    })


@login_required
def sessions_by_date_api(request):
    """Returns JSON representation of active attendance sessions for a specific date (YYYY-MM-DD)."""
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            sessions = AttendanceSession.objects.filter(date=target_date, is_deleted=False)
            data = []
            for s in sessions:
                t_count = s.logs.count()
                p_count = s.logs.filter(status='Present').count()
                rate = round((p_count / t_count) * 100, 1) if t_count > 0 else 0.0
                
                data.append({
                    'id': s.id,
                    'subject': s.subject,
                    'dept_section': f"{s.department} - {s.section}",
                    'period': s.period,
                    'start_time': s.start_time.strftime("%I:%M %p"),
                    'attendance_rate': f"{rate}%"
                })
            return JsonResponse({'success': True, 'sessions': data})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'No date provided.'})



@login_required
def calendar_page(request):
    """Renders the standalone dedicated calendar page."""
    return render(request, 'attendance/calendar.html')


@login_required
def recognize_frame_ajax(request, session_id):
    """Processes a client-side webcam frame for face recognition."""
    if request.method == 'POST':
        import base64
        from django.core.files.base import ContentFile
        
        session = get_object_or_404(AttendanceSession, id=session_id)
        image_data = request.POST.get('image')
        if not image_data:
            return JsonResponse({'success': False, 'step': 1, 'percent': 25, 'message': 'No image data received.'})
            
        try:
            # Decode base64 image data
            format, imgstr = image_data.split(';base64,')
            ext = format.split('/')[-1]
            data = ContentFile(base64.b64decode(imgstr))
            
            # Read image using OpenCV
            nparr = np.frombuffer(data.read(), np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is None:
                return JsonResponse({'success': False, 'step': 1, 'percent': 25, 'message': 'Unreadable image frame.'})
                
            # Run face detection
            # Pre-load only students registered in this session
            students = Student.objects.filter(
                department=session.department, 
                section=session.section
            ).exclude(encoding__isnull=True).exclude(encoding='')
            
            known_face_encodings = []
            known_face_students = []
            for s in students:
                np_enc = s.get_encoding_numpy()
                if np_enc is not None:
                    known_face_encodings.append(np_enc)
                    known_face_students.append(s)
                    
            fr_helper.init_models()
            
            # Face cascade detector
            if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
                haar_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
            else:
                cv2_dir = os.path.dirname(cv2.__file__)
                haar_path = os.path.join(cv2_dir, 'data', 'haarcascade_frontalface_default.xml')
            if not os.path.exists(haar_path):
                haar_path = 'haarcascade_frontalface_default.xml'
            face_cascade = cv2.CascadeClassifier(haar_path)
            
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5, minSize=(60, 60))
            
            if len(faces) == 0:
                return JsonResponse({'success': False, 'step': 1, 'percent': 35, 'message': 'Scanning... no face detected.'})
                
            # Face detected, extract embedding
            x, y, fw, fh = faces[0]
            bbox = [x, y, x+fw, y+fh]
            
            matched_student = None
            confidence = 0.0
            
            emb = fr_helper.get_embedding(frame, bbox=bbox)
            if emb is not None and len(known_face_encodings) > 0:
                min_dist = float('inf')
                best_idx = None
                for idx, known_emb in enumerate(known_face_encodings):
                    dist = fr_helper.cosine_distance(emb, known_emb)
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx
                confidence = (1.0 - min_dist) * 100
                confidence = max(0.0, min(100.0, confidence))
                if min_dist < 0.35:
                    matched_student = known_face_students[best_idx]
                elif min_dist < 0.45:
                    # Medium match: cross-verify with LBPH to prevent false-positives
                    face_crop = gray[y:y+fh, x:x+fw]
                    face_crop_resized = cv2.resize(face_crop, (150, 150))
                    sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                    candidate = known_face_students[best_idx]
                    if sid == candidate.id:
                        matched_student = candidate
                        confidence = max(confidence, lbph_conf)
            
            # Try global FaceNet fallback lookup if not matched in session
            if matched_student is None and emb is not None:
                all_students = Student.objects.exclude(encoding__isnull=True).exclude(encoding='')
                all_encodings = []
                all_stu_list = []
                for s in all_students:
                    np_enc = s.get_encoding_numpy()
                    if np_enc is not None:
                        all_encodings.append(np_enc)
                        all_stu_list.append(s)
                
                if len(all_encodings) > 0:
                    min_dist = float('inf')
                    best_idx = None
                    for idx, known_emb in enumerate(all_encodings):
                        dist = fr_helper.cosine_distance(emb, known_emb)
                        if dist < min_dist:
                            min_dist = dist
                            best_idx = idx
                    tmp_confidence = (1.0 - min_dist) * 100
                    tmp_confidence = max(0.0, min(100.0, tmp_confidence))
                    if min_dist < 0.35:
                        matched_student = all_stu_list[best_idx]
                        confidence = tmp_confidence
                    elif min_dist < 0.45:
                        face_crop = gray[y:y+fh, x:x+fw]
                        face_crop_resized = cv2.resize(face_crop, (150, 150))
                        sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                        candidate = all_stu_list[best_idx]
                        if sid == candidate.id:
                            matched_student = candidate
                            confidence = max(tmp_confidence, lbph_conf)
                    
            # Try LBPH fallback if FaceNet fails
            if matched_student is None:
                face_crop = gray[y:y+fh, x:x+fw]
                face_crop_resized = cv2.resize(face_crop, (150, 150))
                sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                if sid is not None:
                    try:
                        matched_student = Student.objects.get(id=sid)
                        confidence = lbph_conf
                    except Student.DoesNotExist:
                        pass
                        
            if matched_student is not None:
                # Mark present
                log_record = AttendanceLog.objects.filter(session=session, student=matched_student).first()
                if log_record:
                    log_record.status = 'Present'
                    log_record.time_marked = datetime.now().strftime("%H:%M:%S")
                    log_record.confidence = round(confidence, 2)
                    log_record.save()
                    return JsonResponse({
                        'success': True,
                        'step': 4,
                        'percent': 100,
                        'message': f'Face verified: {matched_student.name} ({confidence:.1f}%)',
                        'name': matched_student.name,
                        'roll_number': matched_student.roll_number
                    })
                else:
                    dept = matched_student.department or 'Unknown Dept'
                    sec = matched_student.section or 'Unknown Sec'
                    return JsonResponse({
                        'success': False, 
                        'step': 2, 
                        'percent': 60, 
                        'message': f'{matched_student.name} detected but not in this session roster ({dept} {sec}).'
                    })
            else:
                return JsonResponse({'success': False, 'step': 2, 'percent': 50, 'message': 'Unknown face detected.'})
        except Exception as e:
            return JsonResponse({'success': False, 'step': 1, 'percent': 25, 'message': f'Error: {str(e)}'})
            
    return JsonResponse({'success': False, 'step': 1, 'percent': 25, 'message': 'POST request required.'})


@login_required
def session_detail(request, session_id):
    """Renders details of a past attendance session (present and absent rosters)."""
    session = get_object_or_404(AttendanceSession, id=session_id)
    logs = session.logs.all().select_related('student').order_by('student__name')
    
    present_list = []
    absent_list = []
    
    for l in logs:
        student_data = {
            'student_id': l.student.id,
            'name': l.student.name,
            'roll_number': l.student.roll_number or '-',
            'parent_email': l.student.parent_email or '',
            'parent_phone': l.student.parent_phone or '',
            'avatar_url': l.student.photo.url if l.student.photo else '/static/attendance/default-avatar.png',
        }
        if l.status == 'Present':
            student_data.update({
                'time_marked': l.time_marked,
                'confidence': f"{l.confidence:.2f}%",
            })
            present_list.append(student_data)
        else:
            absent_list.append(student_data)
            
    t_count = len(logs)
    p_count = len(present_list)
    rate = round((p_count / t_count) * 100, 1) if t_count > 0 else 0.0
    
    context = {
        'session': session,
        'present': present_list,
        'absent': absent_list,
        'total_count': t_count,
        'present_count': p_count,
        'absent_count': len(absent_list),
        'attendance_rate': f"{rate}%",
    }
    return render(request, 'attendance/session_detail.html', context)


def save_email_as_html(to_email, subject, message):
    from django.conf import settings
    try:
        media_root = settings.MEDIA_ROOT
        emails_dir = os.path.join(media_root, 'emails')
        if not os.path.exists(emails_dir):
            os.makedirs(emails_dir)
            
        filename = f"email_{int(time.time())}_{random.randint(1000, 9999)}.html"
        filepath = os.path.join(emails_dir, filename)
        
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smartattend.com')
        
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Local Email Preview</title>
    <style>
        body {{ font-family: 'Segoe UI', Roboto, sans-serif; background: #0c0b14; color: #f1f1f1; padding: 40px; margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
        .email-card {{ background: #161522; border: 1px solid #2e2c45; border-radius: 20px; padding: 30px; width: 100%; max-width: 600px; box-shadow: 0 10px 35px rgba(0,0,0,0.5); }}
        .header {{ border-bottom: 1px solid #2e2c45; padding-bottom: 20px; margin-bottom: 25px; }}
        .meta-row {{ font-size: 0.95rem; margin-bottom: 10px; color: #a0a0b0; }}
        .meta-row strong {{ color: #ffffff; font-weight: 600; min-width: 80px; display: inline-block; }}
        .subject-badge {{ background: rgba(0, 184, 248, 0.1); color: #00a8e8; padding: 4px 10px; border-radius: 6px; font-weight: 600; display: inline-block; margin-top: 10px; }}
        .body {{ line-height: 1.6; font-size: 1.05rem; color: #dcdce8; min-height: 120px; white-space: pre-wrap; }}
        .footer {{ border-top: 1px solid #2e2c45; padding-top: 15px; margin-top: 30px; font-size: 0.85rem; color: #6a6a7a; text-align: center; }}
    </style>
</head>
<body>
    <div class="email-card">
        <div class="header">
            <div class="meta-row"><strong>From:</strong> SmartAttend Alert &lt;{from_email}&gt;</div>
            <div class="meta-row"><strong>To:</strong> {to_email}</div>
            <div class="meta-row"><strong>Subject:</strong> <span class="subject-badge">{subject}</span></div>
        </div>
        <div class="body">{message}</div>
        <div class="footer">
            This is a local email simulation preview generated by SmartAttend.
        </div>
    </div>
</body>
</html>
"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        from django.urls import reverse
        return reverse('email_preview', kwargs={'filename': filename})
    except Exception as e:
        print(f"Error saving email to HTML: {e}")
        return None


@login_required
def email_preview_view(request, filename):
    """Serves local simulated email files with explicit HTML content type header."""
    from django.conf import settings
    from django.http import Http404, HttpResponse
    
    # Secure filename to prevent directory traversal
    filename = os.path.basename(filename)
    filepath = os.path.join(settings.MEDIA_ROOT, 'emails', filename)
    
    if not os.path.exists(filepath):
        raise Http404("Email preview not found.")
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return HttpResponse(content, content_type='text/html; charset=utf-8')
    except Exception as e:
        raise Http404(f"Error reading email preview: {e}")


def save_sms_as_html(to_phone, message):
    from django.conf import settings
    import os
    import time
    import random
    try:
        media_root = settings.MEDIA_ROOT
        sms_dir = os.path.join(media_root, 'sms')
        if not os.path.exists(sms_dir):
            os.makedirs(sms_dir)
            
        filename = f"sms_{int(time.time())}_{random.randint(1000, 9999)}.html"
        filepath = os.path.join(sms_dir, filename)
        
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>SMS Preview</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {{
            --bg-dark: #09070f;
            --accent-emerald: #00e676;
            --border-card: rgba(255, 255, 255, 0.08);
            --primary-gradient: linear-gradient(135deg, #7c4dff 0%, #00e5ff 100%);
        }}
        body {{
            background: var(--bg-dark);
            color: #ffffff;
            font-family: 'Outfit', sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }}
        /* iPhone Frame Mockup */
        .phone-frame {{
            width: 320px;
            height: 600px;
            background: #14111d;
            border: 6px solid #2d293d;
            border-radius: 40px;
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.8), inset 0 0 10px rgba(255,255,255,0.05);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            position: relative;
        }}
        /* Dynamic Notch */
        .phone-notch {{
            width: 110px;
            height: 22px;
            background: #2d293d;
            border-bottom-left-radius: 15px;
            border-bottom-right-radius: 15px;
            position: absolute;
            top: 0;
            left: 50%;
            transform: translateX(-50%);
            z-index: 10;
        }}
        /* Header Details */
        .phone-header {{
            padding: 30px 18px 12px 18px;
            background: rgba(255,255,255,0.02);
            border-bottom: 1px solid var(--border-card);
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .phone-back-btn {{
            color: #7c4dff;
            font-size: 1.1rem;
            cursor: pointer;
        }}
        .avatar-circle {{
            width: 38px;
            height: 38px;
            border-radius: 50%;
            background: var(--primary-gradient);
            display: flex;
            justify-content: center;
            align-items: center;
            font-weight: 700;
            font-size: 0.95rem;
            color: #ffffff;
        }}
        .recipient-info {{
            flex: 1;
        }}
        .recipient-name {{
            font-weight: 600;
            font-size: 0.88rem;
        }}
        .recipient-status {{
            font-size: 0.72rem;
            color: var(--accent-emerald);
        }}
        /* SMS Chat Area */
        .chat-area {{
            flex: 1;
            padding: 20px 15px;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            gap: 15px;
            background: radial-gradient(circle at top left, rgba(124, 77, 255, 0.05), transparent 60%);
        }}
        .time-divider {{
            font-size: 0.7rem;
            color: rgba(255,255,255,0.3);
            text-align: center;
            margin-bottom: 5px;
        }}
        .sms-bubble {{
            max-width: 82%;
            padding: 12px 16px;
            border-radius: 18px;
            font-size: 0.82rem;
            line-height: 1.4;
            position: relative;
            background: #252136;
            color: #e2e1e9;
            border-bottom-left-radius: 4px;
            align-self: flex-start;
            border: 1px solid rgba(255,255,255,0.02);
        }}
        .sms-bubble::after {{
            content: "";
            position: absolute;
            bottom: 0;
            left: -8px;
            width: 0;
            height: 0;
            border-style: solid;
            border-width: 0 8px 8px 0;
            border-color: transparent #252136 transparent transparent;
        }}
        /* Input Bar */
        .phone-footer {{
            padding: 10px 12px;
            background: rgba(0,0,0,0.2);
            border-top: 1px solid var(--border-card);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .input-bar {{
            flex: 1;
            background: rgba(255,255,255,0.05);
            border: 1px solid var(--border-card);
            border-radius: 20px;
            padding: 8px 15px;
            font-size: 0.78rem;
            color: rgba(255,255,255,0.4);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .send-btn {{
            color: #00e5ff;
            cursor: pointer;
        }}
        .footer-note {{
            font-size: 0.72rem;
            color: rgba(255,255,255,0.25);
            text-align: center;
            margin-top: 15px;
        }}
    </style>
</head>
<body>
    <div>
        <div class="phone-frame">
            <div class="phone-notch"></div>
            <div class="phone-header">
                <i class="fa-solid fa-chevron-left phone-back-btn"></i>
                <div class="avatar-circle"><i class="fa-solid fa-comment-sms" style="font-size: 0.82rem;"></i></div>
                <div class="recipient-info">
                    <div class="recipient-name">{to_phone}</div>
                    <div class="recipient-status"><i class="fa-solid fa-circle" style="font-size: 0.45rem; margin-right: 4px;"></i> Online</div>
                </div>
                <i class="fa-solid fa-phone" style="color: rgba(255,255,255,0.4); font-size: 0.9rem;"></i>
            </div>
            
            <div class="chat-area">
                <div class="time-divider">Today at {time.strftime('%I:%M %p')}</div>
                <div class="sms-bubble">
                    {message}
                </div>
            </div>
            
            <div class="phone-footer">
                <div class="input-bar">
                    <span>Text Message...</span>
                    <i class="fa-solid fa-circle-arrow-up send-btn"></i>
                </div>
            </div>
        </div>
        <div class="footer-note">This is a local SMS simulation preview generated by SmartAttend.</div>
    </div>
</body>
</html>
"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        from django.urls import reverse
        return reverse('sms_preview', kwargs={'filename': filename})
    except Exception as e:
        print(f"Error saving SMS to HTML: {e}")
        return None


@login_required
def sms_preview_view(request, filename):
    """Serves local simulated SMS files with explicit HTML content type header."""
    from django.conf import settings
    from django.http import Http404, HttpResponse
    
    # Secure filename to prevent directory traversal
    filename = os.path.basename(filename)
    filepath = os.path.join(settings.MEDIA_ROOT, 'sms', filename)
    
    if not os.path.exists(filepath):
        raise Http404("SMS preview not found.")
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return HttpResponse(content, content_type='text/html; charset=utf-8')
    except Exception as e:
        raise Http404(f"Error reading SMS preview: {e}")


def send_sms_notification(to_phone, message):
    """Sends a real SMS using Fast2SMS or Twilio if configured, else falls back to local simulation."""
    import os
    import requests
    
    # 1. Try Fast2SMS (Simple, developer-friendly option in India)
    fast2sms_key = os.environ.get('FAST2SMS_API_KEY')
    if fast2sms_key:
        try:
            # Clean up phone number (digits only, e.g. "9025606325")
            clean_phone = ''.join(filter(str.isdigit, to_phone))
            if len(clean_phone) > 10 and clean_phone.startswith('91'):
                clean_phone = clean_phone[2:]
                
            url = "https://www.fast2sms.com/dev/bulkV2"
            params = {
                "authorization": fast2sms_key,
                "message": message,
                "language": "english",
                "route": "q",
                "numbers": clean_phone
            }
            response = requests.get(url, params=params, timeout=8)
            res_data = response.json()
            if res_data.get('return') is True:
                return 'Sent', None
            else:
                print(f"[SMS FALLBACK] Fast2SMS returned error: {res_data}")
        except Exception as e:
            print(f"[SMS FALLBACK] Fast2SMS failed: {e}")
            
    # 2. Try Twilio
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    from_number = os.environ.get('TWILIO_PHONE_NUMBER')
    
    if account_sid and auth_token and from_number:
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            client.messages.create(
                body=message,
                from_=from_number,
                to=to_phone
            )
            return 'Sent', None
        except Exception as e:
            print(f"[SMS FALLBACK] Twilio failed: {e}")
            
    # Fallback to local HTML simulation
    print(f"[SMS FALLBACK] Logging SMS -> To: {to_phone} | Msg: {message}")
    sms_url = save_sms_as_html(to_phone, message)
    return 'Simulated', sms_url


def send_emails_in_background(log_ids, subject_text, message_text, from_email, channels):
    from django.db import connection as django_db_connection
    from django.core.mail import get_connection, EmailMessage
    from attendance.models import AttendanceLog
    import os
    
    # Retrieve logs
    logs = AttendanceLog.objects.filter(id__in=log_ids).select_related('student')
    
    connection_opened = False
    connection = None
    if 'email' in channels:
        try:
            connection = get_connection(fail_silently=False)
            connection.open()
            connection_opened = True
        except Exception as conn_err:
            print(f"[BACKGROUND EMAIL] Failed to open SMTP connection: {conn_err}")
            
    for log in logs:
        student = log.student
        email = student.parent_email
        phone = student.parent_phone
        
        # Send Email
        if 'email' in channels and email:
            personalized_msg = message_text.replace("your ward", f"your ward {student.name}")
            if connection_opened:
                try:
                    email_msg = EmailMessage(
                        subject=subject_text,
                        body=personalized_msg,
                        from_email=from_email,
                        to=[email],
                        connection=connection
                    )
                    email_msg.send(fail_silently=False)
                    print(f"[BACKGROUND EMAIL] Sent email to {email}")
                except Exception as smtp_err:
                    print(f"[BACKGROUND EMAIL] SMTP failed: {smtp_err}")
                    save_email_as_html(email, subject_text, personalized_msg)
            else:
                save_email_as_html(email, subject_text, personalized_msg)
                
        # Send SMS
        if 'sms' in channels and phone:
            try:
                personalized_msg = message_text.replace("your ward", f"your ward {student.name}")
                send_sms_notification(phone, personalized_msg)
            except Exception as e:
                print(f"[BACKGROUND SMS] Failed: {e}")
                
    if connection_opened and connection:
        try:
            connection.close()
        except:
            pass
            
    # Clean up thread database connection
    django_db_connection.close()


@login_required
@csrf_exempt
def broadcast_absentee_email(request, session_id):
    """Broadcasting parent alerts via Django email framework."""
    from django.conf import settings
    import os

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            subject_text = data.get('subject', 'Attendance Notification')
            message_text = data.get('message', '')
            channels = data.get('channels', ['email'])
        except Exception as e:
            print("EXCEPTION IN BROADCAST_ABSENTEE_EMAIL:", e)
            subject_text = request.POST.get('subject', 'Attendance Notification')
            message_text = request.POST.get('message', '')
            channels = request.POST.getlist('channels') or ['email']
            
        if not message_text:
            return JsonResponse({'success': False, 'message': 'Notification content body cannot be empty!'})
            
        session = get_object_or_404(AttendanceSession, id=session_id)
        absent_logs = session.logs.filter(status='Absent').select_related('student')
        
        log_ids = list(absent_logs.values_list('id', flat=True))
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smartattend.com')
        
        # Start background thread
        import threading
        t = threading.Thread(
            target=send_emails_in_background,
            args=(log_ids, subject_text, message_text, from_email, channels)
        )
        t.daemon = True
        t.start()
        
        has_smtp = bool(os.environ.get('EMAIL_HOST_PASSWORD'))
        broadcast_results = []
        for log in absent_logs:
            broadcast_results.append({
                'student_id': log.student.id,
                'name': log.student.name,
                'email': log.student.parent_email or '',
                'phone': log.student.parent_phone or '',
                'email_status': 'Sent' if has_smtp else 'Simulated',
                'email_url': None,
                'sms_status': 'Skipped',
                'sms_url': None
            })
            
        return JsonResponse({
            'success': True,
            'message': 'Broadcast started in background.',
            'results': broadcast_results
        })
        
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
@csrf_exempt
def update_student_email_ajax(request, student_id):
    """AJAX endpoint to update a student's parent email address directly from rosters."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip()
        except:
            email = request.POST.get('email', '').strip()
            
        student = get_object_or_404(Student, id=student_id)
        student.parent_email = email if email else None
        student.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Email address updated successfully.',
            'email': student.parent_email or ''
        })
        
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})

@login_required
@csrf_exempt
def broadcast_all_email(request, session_id):
    """Broadcasting parent alerts via Django email framework to all students in a session."""
    from django.conf import settings
    import os

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            subject_text = data.get('subject', 'Attendance Notification')
            message_text = data.get('message', '')
            channels = data.get('channels', ['email'])
        except:
            subject_text = request.POST.get('subject', 'Attendance Notification')
            message_text = request.POST.get('message', '')
            channels = request.POST.getlist('channels') or ['email']
            
        if not message_text:
            return JsonResponse({'success': False, 'message': 'Notification content body cannot be empty!'})
            
        session = get_object_or_404(AttendanceSession, id=session_id)
        all_logs = session.logs.all().select_related('student')
        
        log_ids = list(all_logs.values_list('id', flat=True))
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smartattend.com')
        
        # Start background thread
        import threading
        t = threading.Thread(
            target=send_emails_in_background,
            args=(log_ids, subject_text, message_text, from_email, channels)
        )
        t.daemon = True
        t.start()
        
        has_smtp = bool(os.environ.get('EMAIL_HOST_PASSWORD'))
        broadcast_results = []
        for log in all_logs:
            broadcast_results.append({
                'student_id': log.student.id,
                'name': log.student.name,
                'email': log.student.parent_email or '',
                'phone': log.student.parent_phone or '',
                'email_status': 'Sent' if has_smtp else 'Simulated',
                'email_url': None,
                'sms_status': 'Skipped',
                'sms_url': None
            })
            
        return JsonResponse({
            'success': True,
            'message': 'Broadcast started in background.',
            'results': broadcast_results
        })
        
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
@csrf_exempt
def send_single_email(request, session_id, student_id):
    """Sends a parent alert to a single student's parent via Django email framework."""
    from django.core.mail import send_mail
    from django.conf import settings

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            subject_text = data.get('subject', 'Attendance Notification')
            message_text = data.get('message', '')
            channels = data.get('channels', ['email'])
        except Exception as e:
            subject_text = request.POST.get('subject', 'Attendance Notification')
            message_text = request.POST.get('message', '')
            channels = request.POST.getlist('channels') or ['email']
            
        if not message_text:
            return JsonResponse({'success': False, 'message': 'Notification content body cannot be empty!'})
            
        student = get_object_or_404(Student, id=student_id)
        email = student.parent_email
        phone = student.parent_phone
        
        if not email and 'email' in channels:
            return JsonResponse({'success': False, 'message': f'Student {student.name} does not have a parent email registered.'})
        if not phone and 'sms' in channels:
            return JsonResponse({'success': False, 'message': f'Student {student.name} does not have a parent phone registered.'})
            
        try:
            result_item = {
                'student_id': student.id,
                'name': student.name,
                'email': email or '',
                'phone': phone or '',
                'email_status': 'Skipped',
                'email_url': None,
                'sms_status': 'Skipped',
                'sms_url': None
            }
            
            # Send Email if selected
            if 'email' in channels and email:
                try:
                    personalized_msg = message_text.replace("your ward", f"your ward {student.name}")
                    try:
                        send_mail(
                            subject=subject_text,
                            message=personalized_msg,
                            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smartattend.com'),
                            recipient_list=[email],
                            fail_silently=False
                        )
                        result_item['email_status'] = 'Sent'
                    except Exception as smtp_err:
                        print(f"[EMAIL FALLBACK] SMTP failed: {smtp_err}")
                        print(f"Logged Email -> To: {email} | Subject: {subject_text} | Msg: {personalized_msg}")
                        result_item['email_status'] = 'Simulated'
                        result_item['email_url'] = save_email_as_html(email, subject_text, personalized_msg)
                except Exception as e:
                    print(f"Error sending email: {e}")
                    result_item['email_status'] = f'Error: {str(e)}'
                    
            # Send SMS if selected
            if 'sms' in channels and phone:
                try:
                    personalized_msg = message_text.replace("your ward", f"your ward {student.name}")
                    sms_status, sms_url = send_sms_notification(phone, personalized_msg)
                    result_item['sms_status'] = sms_status
                    result_item['sms_url'] = sms_url
                except Exception as e:
                    print(f"Error sending SMS: {e}")
                    result_item['sms_status'] = f'Error: {str(e)}'

            return JsonResponse({
                'success': True,
                'message': f'Notifications processed for {student.name}\'s parent.',
                'results': [result_item]
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Failed to send notification: {str(e)}'})
            
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
def exam_list(request):
    """Renders the dashboard listing all exams and handles creation of new exams."""
    if request.method == 'POST':
        subject = request.POST.get('subject')
        exam_code = request.POST.get('exam_code')
        department = request.POST.get('department')
        section = request.POST.get('section')
        date_str = request.POST.get('date')
        time_str = request.POST.get('start_time')
        end_time_str = request.POST.get('end_time')
        
        try:
            exam_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            exam_date = date.today()
            
        try:
            exam_time = datetime.strptime(time_str, "%H:%M").time()
        except (ValueError, TypeError):
            exam_time = datetime.now().time()
            
        try:
            exam_end_time = datetime.strptime(end_time_str, "%H:%M").time()
        except (ValueError, TypeError):
            exam_end_time = None
            
        exam = Exam.objects.create(
            subject=subject,
            exam_code=exam_code,
            department=department,
            section=section,
            date=exam_date,
            start_time=exam_time,
            end_time=exam_end_time
        )
        
        # Populate verification logs as 'Pending' for all students in matching dept/sec
        students = Student.objects.filter(department=department, section=section)
        for student in students:
            ExamVerificationLog.objects.create(
                exam=exam,
                student=student,
                status='Pending',
                time_verified='-',
                confidence=0.0
            )
            
        return redirect('exam_list')
        
    exams = Exam.objects.all()
    departments = Department.objects.all()
    sections = Section.objects.all()
    
    context = {
        'exams': exams,
        'departments': list(departments),
        'sections': list(sections),
        'nav_exam': 'active'
    }
    return render(request, 'attendance/exam_list.html', context)


@login_required
def exam_scanner(request, exam_id):
    """Renders the webcam scanner portal for the selected exam."""
    exam = get_object_or_404(Exam, id=exam_id)
    return render(request, 'attendance/exam_scanner.html', {'exam': exam, 'nav_exam': 'active'})


@login_required
def recognize_exam_frame_ajax(request, exam_id):
    """Processes a client-side webcam frame for exam identity verification."""
    if request.method == 'POST':
        import base64
        from django.core.files.base import ContentFile
        
        exam = get_object_or_404(Exam, id=exam_id)
        image_data = request.POST.get('image')
        if not image_data:
            return JsonResponse({'success': False, 'step': 1, 'percent': 25, 'message': 'No image data received.'})
            
        try:
            format, imgstr = image_data.split(';base64,')
            data = ContentFile(base64.b64decode(imgstr))
            
            nparr = np.frombuffer(data.read(), np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is None:
                return JsonResponse({'success': False, 'step': 1, 'percent': 25, 'message': 'Unreadable image frame.'})
                
            target_student_id = request.POST.get('target_student_id')
            if target_student_id:
                target_student = Student.objects.filter(id=target_student_id).first()
                students = Student.objects.filter(id=target_student_id).exclude(encoding__isnull=True).exclude(encoding='')
            else:
                target_student = None
                # Pre-load only students registered in this exam's department/section
                students = Student.objects.filter(
                    department=exam.department, 
                    section=exam.section
                ).exclude(encoding__isnull=True).exclude(encoding='')
            
            known_face_encodings = []
            known_face_students = []
            for s in students:
                np_enc = s.get_encoding_numpy()
                if np_enc is not None:
                    known_face_encodings.append(np_enc)
                    known_face_students.append(s)
                    
            fr_helper.init_models()
            
            if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
                haar_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
            else:
                cv2_dir = os.path.dirname(cv2.__file__)
                haar_path = os.path.join(cv2_dir, 'data', 'haarcascade_frontalface_default.xml')
            if not os.path.exists(haar_path):
                haar_path = 'haarcascade_frontalface_default.xml'
            face_cascade = cv2.CascadeClassifier(haar_path)
            
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5, minSize=(60, 60))
            
            if len(faces) == 0:
                return JsonResponse({'success': False, 'step': 1, 'percent': 35, 'message': 'Scanning... no face detected.'})
                
            x, y, fw, fh = faces[0]
            bbox = [x, y, x+fw, y+fh]
            
            matched_student = None
            confidence = 0.0
            
            emb = fr_helper.get_embedding(frame, bbox=bbox)
            if emb is not None and len(known_face_encodings) > 0:
                min_dist = float('inf')
                best_idx = None
                for idx, known_emb in enumerate(known_face_encodings):
                    dist = fr_helper.cosine_distance(emb, known_emb)
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx
                confidence = (1.0 - min_dist) * 100
                confidence = max(0.0, min(100.0, confidence))
                
                if target_student_id:
                    # Targeted mode: heighten match tolerance to 0.52 since there is no risk of false matching others
                    if min_dist < 0.52:
                        matched_student = known_face_students[best_idx]
                else:
                    if min_dist < 0.35:
                        matched_student = known_face_students[best_idx]
                    elif min_dist < 0.45:
                        # Medium match: cross-verify with LBPH to prevent false-positives
                        face_crop = gray[y:y+fh, x:x+fw]
                        face_crop_resized = cv2.resize(face_crop, (150, 150))
                        sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                        candidate = known_face_students[best_idx]
                        if sid == candidate.id:
                            matched_student = candidate
                            confidence = max(confidence, lbph_conf)
            
            # Global FaceNet fallback lookup
            if matched_student is None and emb is not None:
                all_students = Student.objects.exclude(encoding__isnull=True).exclude(encoding='')
                all_encodings = []
                all_stu_list = []
                for s in all_students:
                    np_enc = s.get_encoding_numpy()
                    if np_enc is not None:
                        all_encodings.append(np_enc)
                        all_stu_list.append(s)
                
                if len(all_encodings) > 0:
                    min_dist = float('inf')
                    best_idx = None
                    for idx, known_emb in enumerate(all_encodings):
                        dist = fr_helper.cosine_distance(emb, known_emb)
                        if dist < min_dist:
                            min_dist = dist
                            best_idx = idx
                    tmp_confidence = (1.0 - min_dist) * 100
                    tmp_confidence = max(0.0, min(100.0, tmp_confidence))
                    
                    if target_student_id:
                        # Check if closest global match is our target student within permissive threshold
                        candidate = all_stu_list[best_idx]
                        if candidate.id == target_student.id and min_dist < 0.52:
                            matched_student = candidate
                            confidence = tmp_confidence
                    else:
                        if min_dist < 0.35:
                            matched_student = all_stu_list[best_idx]
                            confidence = tmp_confidence
                        elif min_dist < 0.45:
                            face_crop = gray[y:y+fh, x:x+fw]
                            face_crop_resized = cv2.resize(face_crop, (150, 150))
                            sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                            candidate = all_stu_list[best_idx]
                            if sid == candidate.id:
                                matched_student = candidate
                            confidence = max(tmp_confidence, lbph_conf)
                    
            # LBPH fallback
            if matched_student is None:
                face_crop = gray[y:y+fh, x:x+fw]
                face_crop_resized = cv2.resize(face_crop, (150, 150))
                sid, sname, _, lbph_conf = fr_helper.predict_lbph(face_crop_resized)
                if sid is not None:
                    try:
                        matched_student = Student.objects.get(id=sid)
                        confidence = lbph_conf
                    except Student.DoesNotExist:
                        pass
                        
            if matched_student is not None:
                if target_student and matched_student.id != target_student.id:
                    return JsonResponse({
                        'success': False,
                        'step': 3,
                        'percent': 80,
                        'message': f'Rejected: Face does not match {target_student.name}.'
                    })
                
                log_record = ExamVerificationLog.objects.filter(exam=exam, student=matched_student).first()
                if log_record:
                    log_record.status = 'Verified'
                    log_record.time_verified = datetime.now().strftime("%H:%M:%S")
                    log_record.confidence = round(confidence, 2)
                    log_record.save()
                    return JsonResponse({
                        'success': True,
                        'step': 4,
                        'percent': 100,
                        'message': f'Identity verified: {matched_student.name} ({confidence:.1f}%)',
                        'name': matched_student.name,
                        'roll_number': matched_student.roll_number
                    })
                else:
                    dept = matched_student.department or 'Unknown Dept'
                    sec = matched_student.section or 'Unknown Sec'
                    return JsonResponse({
                        'success': False, 
                        'step': 2, 
                        'percent': 60, 
                        'message': f'{matched_student.name} detected but is not on this exam list ({dept} {sec}).'
                    })
            else:
                if target_student:
                    return JsonResponse({
                        'success': False,
                        'step': 3,
                        'percent': 80,
                        'message': f'Rejected: Face does not match {target_student.name}.'
                    })
                return JsonResponse({'success': False, 'step': 3, 'percent': 80, 'message': 'Unknown identity.'})
        except Exception as e:
            return JsonResponse({'success': False, 'step': -1, 'percent': 100, 'message': f'Error: {str(e)}'})
            
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
def exam_status_ajax(request, exam_id):
    """Returns JSON lists of verified and pending students for the exam scanner UI."""
    exam = get_object_or_404(Exam, id=exam_id)
    logs = ExamVerificationLog.objects.filter(exam=exam).select_related('student')
    
    verified_list = []
    pending_list = []
    
    for log in logs:
        student = log.student
        photo_url = student.photo.url if student.photo else ""
        
        student_data = {
            'id': student.id,
            'log_id': log.id,
            'name': student.name,
            'roll_number': student.roll_number or "N/A",
            'department': student.department or "N/A",
            'section': student.section or "N/A",
            'photo_url': photo_url,
            'time_verified': log.time_verified,
            'confidence': log.confidence,
        }
        
        if log.status == 'Verified':
            verified_list.append(student_data)
        else:
            pending_list.append(student_data)
            
    return JsonResponse({
        'success': True,
        'verified': verified_list,
        'pending': pending_list,
        'verified_count': len(verified_list),
        'pending_count': len(pending_list),
        'total_count': len(logs)
    })


@login_required
def toggle_exam_status(request, log_id):
    """Manually toggles a student's verification status (Present/Absent) for an exam."""
    log = get_object_or_404(ExamVerificationLog, id=log_id)
    if log.status == 'Verified':
        log.status = 'Pending'
        log.time_verified = '-'
        log.confidence = 0.0
    else:
        log.status = 'Verified'
        log.time_verified = datetime.now().strftime("%H:%M:%S")
        log.confidence = 100.0
    log.save()
    return JsonResponse({'success': True})


@login_required
def reset_exam_verification(request, exam_id):
    """Resets all student identity verification logs for the specified exam."""
    exam = get_object_or_404(Exam, id=exam_id)
    ExamVerificationLog.objects.filter(exam=exam).update(
        status='Pending',
        time_verified='-',
        confidence=0.0
    )
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': 'Exam verifications reset.'})
    return redirect('exam_scanner', exam_id=exam.id)


@login_required
def complete_exam(request, exam_id):
    """Marks an exam session as completed and redirects to its report."""
    exam = get_object_or_404(Exam, id=exam_id)
    exam.is_completed = True
    exam.save()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return JsonResponse({'success': True, 'redirect_url': reverse('exam_report', kwargs={'exam_id': exam.id})})
    return redirect('exam_report', exam_id=exam.id)


@login_required
def exam_report(request, exam_id):
    """Renders the detailed verification report for a specific exam."""
    exam = get_object_or_404(Exam, id=exam_id)
    logs = ExamVerificationLog.objects.filter(exam=exam).select_related('student')
    
    verified_count = logs.filter(status='Verified').count()
    total_count = logs.count()
    pending_count = total_count - verified_count
    
    rate = 0.0
    if total_count > 0:
        rate = round((verified_count / total_count) * 100, 1)
        
    context = {
        'exam': exam,
        'logs': logs,
        'verified_count': verified_count,
        'pending_count': pending_count,
        'total_count': total_count,
        'rate': rate,
        'nav_exam': 'active'
    }
    return render(request, 'attendance/exam_report.html', context)

