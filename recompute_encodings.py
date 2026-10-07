import os
import sys
import django
import cv2
import json

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'face_attendance_project.settings')
django.setup()

from attendance.models import Student
import attendance.face_rec_helper as fr_helper

def migrate_database():
    print("==================================================")
    print("   Django Face Recognition Migration Script       ")
    print("   Converting 128-D (dlib) -> 512-D (FaceNet)    ")
    print("==================================================")
    
    students = Student.objects.all()
    if not students.exists():
        print("No students found in the database. Nothing to migrate.")
        return
        
    print(f"Found {students.count()} students. Starting migration...")
    
    # Initialize FaceNet & MTCNN models
    fr_helper.init_models()
    
    success_count = 0
    fail_count = 0
    
    for s in students:
        if not s.photo:
            print(f"[-] Student {s.name}: Missing profile image. Skipping.")
            fail_count += 1
            continue
            
        photo_path = s.photo.path
        if not os.path.exists(photo_path):
            print(f"[-] Student {s.name}: Image file not found at {photo_path}. Skipping.")
            fail_count += 1
            continue
            
        print(f"[*] Processing Student: {s.name}...")
        
        try:
            img = cv2.imread(photo_path)
            if img is not None:
                # Calculate new 512-D FaceNet embedding
                emb = fr_helper.get_embedding(img)
                if emb is not None:
                    # Update encoding field
                    s.encoding = json.dumps(emb.tolist())
                    s.save(update_fields=['encoding'])
                    print(f"[+] Successfully generated 512-D FaceNet embedding for {s.name}.")
                    success_count += 1
                else:
                    print(f"[-] MTCNN/FaceNet could not find a face in {s.name}'s image.")
                    fail_count += 1
            else:
                print(f"[-] Failed to read image file for {s.name}.")
                fail_count += 1
        except Exception as e:
            print(f"[-] Error processing {s.name}: {e}")
            fail_count += 1
            
    print("\n--------------------------------------------------")
    print(f"Migration completed! Success: {success_count}, Failed: {fail_count}")
    print("--------------------------------------------------")
    
    # Now retrain the LBPH fallback model
    print("\nRetraining LBPH Fallback model...")
    lbph_success = fr_helper.train_lbph_model()
    if lbph_success:
        print("[+] LBPH Fallback model trained and saved successfully.")
    else:
        print("[-] LBPH Fallback model training failed.")
        
    print("==================================================")

if __name__ == "__main__":
    migrate_database()
