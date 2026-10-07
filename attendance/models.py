import json
import cv2
import numpy as np
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class Student(models.Model):
    name = models.CharField(max_length=100)
    roll_number = models.CharField(max_length=50, blank=True, null=True, help_text="Roll number of the student.")
    department = models.CharField(max_length=100, blank=True, null=True, help_text="Department, e.g. CSE, MECH, ECE.")
    section = models.CharField(max_length=50, blank=True, null=True, help_text="Section, e.g. A, B, I, N.")
    parent_email = models.EmailField(blank=True, null=True, help_text="Parent contact email address.")
    parent_phone = models.CharField(max_length=20, blank=True, null=True, help_text="Parent contact phone number.")
    photo = models.ImageField(upload_to='students/')
    encoding = models.TextField(
        blank=True, 
        null=True, 
        help_text="512-dimensional face encoding vector stored as a JSON array of floats."
    )
    processing_status = models.TextField(
        blank=True, 
        null=True, 
        default='{"step": 0, "text": "Queued for processing...", "percent": 5}',
        help_text="JSON representation of current background processing status."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        photo_changed = False
        
        if not is_new:
            try:
                old_student = Student.objects.get(pk=self.pk)
                if old_student.photo != self.photo:
                    photo_changed = True
            except Student.DoesNotExist:
                pass
        
        super().save(*args, **kwargs)
        
        # If new, photo changed, or encoding is missing, calculate the encoding in a background thread
        trigger_encoding = False
        if is_new or photo_changed or not self.encoding:
            trigger_encoding = True
            
        # Avoid recursive background thread spawning when only status is updated
        if kwargs.get('update_fields'):
            u_fields = kwargs.get('update_fields')
            if 'processing_status' in u_fields and 'encoding' not in u_fields:
                trigger_encoding = False
                
        if trigger_encoding:
            import threading
            
            def run_background_encoding():
                try:
                    # Fetch fresh instance to ensure correct context in background thread
                    student_obj = Student.objects.get(pk=self.pk)
                    
                    # Step 1: Initializing
                    student_obj.processing_status = json.dumps({"step": 1, "text": "Initializing database record...", "percent": 15})
                    student_obj.save(update_fields=['processing_status'])
                    
                    image_path = student_obj.photo.path
                    
                    # Step 2: Reading image file
                    student_obj.processing_status = json.dumps({"step": 2, "text": "Reading uploaded photo file...", "percent": 30})
                    student_obj.save(update_fields=['processing_status'])
                    
                    import time
                    time.sleep(1.0) # Allow filesystem flush and show step animation
                    
                    img = cv2.imread(image_path)
                    if img is not None:
                        # Step 3: Face detection & embeddings extraction
                        student_obj.processing_status = json.dumps({"step": 3, "text": "Generating FaceNet embeddings...", "percent": 60})
                        student_obj.save(update_fields=['processing_status'])
                        
                        import attendance.face_rec_helper as fr_helper
                        # Extract 512-D FaceNet embedding
                        emb = fr_helper.get_embedding(img)
                        
                        if emb is not None:
                            student_obj.encoding = json.dumps(emb.tolist())
                            
                            # Step 4: Retraining LBPH fallback model
                            student_obj.processing_status = json.dumps({"step": 4, "text": "Training LBPH face classifier...", "percent": 85})
                            student_obj.save(update_fields=['encoding', 'processing_status'])
                            
                            print(f"[SUCCESS] Calculated 512-D FaceNet embedding in background for: {student_obj.name}")
                            
                            # Retrain the LBPH fallback model since database students list changed
                            fr_helper.train_lbph_model()
                            
                            # Step 5: Finished and Active
                            student_obj.processing_status = json.dumps({"step": 5, "text": "Profile active!", "percent": 100})
                            student_obj.save(update_fields=['processing_status'])
                        else:
                            student_obj.processing_status = json.dumps({"step": -1, "text": "Failed: No face detected in photo", "percent": 100})
                            student_obj.save(update_fields=['processing_status'])
                            print(f"[WARNING] FaceNet: No face found in uploaded photo for student: {student_obj.name}")
                    else:
                        student_obj.processing_status = json.dumps({"step": -1, "text": "Failed: Image is unreadable", "percent": 100})
                        student_obj.save(update_fields=['processing_status'])
                        print(f"[ERROR] Failed to read uploaded photo at: {image_path}")
                except Exception as e:
                    try:
                        student_obj = Student.objects.get(pk=self.pk)
                        student_obj.processing_status = json.dumps({"step": -1, "text": f"Error: {str(e)}", "percent": 100})
                        student_obj.save(update_fields=['processing_status'])
                    except Exception:
                        pass
                    print(f"[ERROR] Failed to extract face encoding for {self.pk} in background: {e}")
                finally:
                    from django.db import connection
                    connection.close() # Clean up connection in thread
            
            from django.db import transaction
            transaction.on_commit(lambda: threading.Thread(target=run_background_encoding, daemon=True).start())

    def get_encoding_numpy(self):
        """Decode the stored JSON string back to a numpy array for recognition."""
        if self.encoding:
            try:
                return np.array(json.loads(self.encoding))
            except Exception as e:
                print(f"[ERROR] Failed to decode face encoding for {self.name}: {e}")
                return None
        return None

    def __str__(self):
        return f"{self.name} ({self.roll_number})"

class AttendanceSession(models.Model):
    subject = models.CharField(max_length=150)
    department = models.CharField(max_length=100)
    section = models.CharField(max_length=50)
    period = models.CharField(max_length=50)
    date = models.DateField(auto_now_add=True)
    start_time = models.TimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-date', '-start_time']

    def __str__(self):
        return f"{self.subject} - {self.department} {self.section} - {self.date} ({self.period})"

    @property
    def dept_section(self):
        return f"{self.department} / {self.section}"

    @property
    def present_total(self):
        t_count = self.logs.count()
        p_count = self.logs.filter(status='Present').count()
        return f"{p_count} / {t_count}"

    @property
    def attendance_rate(self):
        t_count = self.logs.count()
        p_count = self.logs.filter(status='Present').count()
        if t_count > 0:
            rate = round((p_count / t_count) * 100, 1)
            return f"{rate}%"
        return "0.0%"

class AttendanceLog(models.Model):
    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name='logs')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='logs')
    status = models.CharField(max_length=20, default='Absent', help_text="Present or Absent")
    time_marked = models.CharField(max_length=20, default='-', help_text="Time marked Present, or - if Absent")
    confidence = models.FloatField(default=0.0, help_text="Face recognition confidence percentage")
    updated_at = models.DateTimeField(auto_now=True)
    date = models.DateField(auto_now_add=True)
    time = models.TimeField(auto_now_add=True)

    class Meta:
        ordering = ['student__name']
        unique_together = ('session', 'student')

    def __str__(self):
        return f"{self.student.name} - {self.session.subject} - {self.status}"

class FacultyProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    full_name = models.CharField(max_length=150)
    department = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.full_name} ({self.department})"

@receiver(post_save, sender=User)
def create_faculty_profile(sender, instance, created, **kwargs):
    if created:
        FacultyProfile.objects.get_or_create(
            user=instance,
            defaults={
                'full_name': instance.username.capitalize(),
                'department': 'CSE'
            }
        )


class Exam(models.Model):
    subject = models.CharField(max_length=150)
    exam_code = models.CharField(max_length=100)
    department = models.CharField(max_length=100)
    section = models.CharField(max_length=50)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_completed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-date', '-start_time']

    def __str__(self):
        return f"{self.subject} ({self.exam_code}) - {self.department} {self.section}"

    @property
    def dept_section(self):
        return f"{self.department} / {self.section}"

    @property
    def verified_total(self):
        t_count = self.verification_logs.count()
        v_count = self.verification_logs.filter(status='Verified').count()
        return f"{v_count} / {t_count}"

    @property
    def verification_rate(self):
        t_count = self.verification_logs.count()
        v_count = self.verification_logs.filter(status='Verified').count()
        if t_count > 0:
            rate = round((v_count / t_count) * 100, 1)
            return f"{rate}%"
        return "0.0%"


class ExamVerificationLog(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='verification_logs')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='exam_logs')
    status = models.CharField(max_length=20, default='Pending', help_text="Pending or Verified")
    time_verified = models.CharField(max_length=20, default='-', help_text="Time verified, or - if pending")
    confidence = models.FloatField(default=0.0, help_text="Face verification confidence percentage")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student__name']
        unique_together = ('exam', 'student')

    def __str__(self):
        return f"{self.student.name} - {self.exam.subject} - {self.status}"


class Subject(models.Model):
    name = models.CharField(max_length=150, unique=True)
    
    class Meta:
        ordering = ['name']
        
    def __str__(self):
        return self.name


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, blank=True, null=True, help_text="e.g. CSE, ECE")
    
    class Meta:
        ordering = ['name']
        
    def __str__(self):
        if self.code:
            return f"{self.code} - {self.name}"
        return self.name


class Section(models.Model):
    name = models.CharField(max_length=50, unique=True)
    
    class Meta:
        ordering = ['name']
        
    def __str__(self):
        return self.name


class Period(models.Model):
    name = models.CharField(max_length=50, unique=True)
    
    class Meta:
        ordering = ['name']
        
    def __str__(self):
        return self.name



