import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'face_attendance_project.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth.models import User
from attendance.views import broadcast_absentee_email
from attendance.models import AttendanceSession, Student, AttendanceLog

# Get or create a session and student
session = AttendanceSession.objects.first()
if not session:
    session = AttendanceSession.objects.create(
        subject="Test Subject",
        department="CSE",
        section="A",
        period="1"
    )

student = Student.objects.first()
if not student:
    # We need a dummy photo
    from django.core.files.uploadedfile import SimpleUploadedFile
    dummy_photo = SimpleUploadedFile("temp.jpg", b"file_content", content_type="image/jpeg")
    student = Student.objects.create(
        name="Test Student",
        roll_number="12345",
        department="CSE",
        section="A",
        parent_email="test@example.com",
        photo=dummy_photo
    )

# Create an absentee log for the student
AttendanceLog.objects.get_or_create(
    session=session,
    student=student,
    defaults={'status': 'Absent'}
)

factory = RequestFactory()
# Simulate a POST request with JSON body
payload = {'subject': 'Test Subject', 'message': 'Dear Parent, your ward was absent.'}
request = factory.post(
    f'/session/{session.id}/broadcast-email/',
    data=json.dumps(payload),
    content_type='application/json'
)

# Mock login
user = User.objects.first() or User.objects.create_user('testuser', 'test@example.com', 'password')
request.user = user

print("Request body:", request.body)
try:
    data = json.loads(request.body)
    print("Parsed data:", data)
except Exception as e:
    print("JSON loads exception:", e)

try:
    response = broadcast_absentee_email(request, session.id)
    print("Response status code:", response.status_code)
    print("Response content:", response.content.decode('utf-8'))
except Exception as e:
    import traceback
    print("Exception occurred:")
    traceback.print_exc()
