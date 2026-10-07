import os
import pickle
import json
import cv2
import numpy as np
import torch
from django.conf import settings
from facenet_pytorch import MTCNN, InceptionResnetV1

# Define model paths dynamically using Django's BASE_DIR
MODEL_DIR = os.path.join(settings.BASE_DIR, "Models")
FACENET_DIR = os.path.join(MODEL_DIR, "facenet_model")
LBPH_DIR = os.path.join(MODEL_DIR, "lbph_model")

os.makedirs(FACENET_DIR, exist_ok=True)
os.makedirs(LBPH_DIR, exist_ok=True)

LBPH_YML_PATH = os.path.join(LBPH_DIR, "lbph_model.yml")
LBPH_LABELS_PATH = os.path.join(LBPH_DIR, "lbph_labels.pkl")

# Global device and models setup
device = None
mtcnn = None
facenet_model = None

# Haar Cascade for fast face detection
if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
    HAAR_PATH = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
else:
    cv2_dir = os.path.dirname(cv2.__file__)
    HAAR_PATH = os.path.join(cv2_dir, 'data', 'haarcascade_frontalface_default.xml')

if not os.path.exists(HAAR_PATH):
    HAAR_PATH = 'haarcascade_frontalface_default.xml'

face_cascade = cv2.CascadeClassifier(HAAR_PATH)

def init_models():
    """Initializes FaceNet and MTCNN models (CPU/GPU)."""
    global device, mtcnn, facenet_model
    if mtcnn is not None and facenet_model is not None:
        return True

    try:
        device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        # MTCNN for single face detection
        mtcnn = MTCNN(keep_all=False, device=device)
        # FaceNet pre-trained on VGGFace2
        facenet_model = InceptionResnetV1(pretrained='vggface2').eval().to(device)
        print(f"[INFO] FaceNet/MTCNN initialized successfully on: {device}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to initialize FaceNet/MTCNN: {e}")
        return False

def cosine_similarity(a, b):
    """Calculates cosine similarity between two vectors."""
    dot_val = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_val / (norm_a * norm_b)

def cosine_distance(a, b):
    """Calculates cosine distance between two vectors."""
    return 1.0 - cosine_similarity(a, b)

def get_embedding(img_bgr, bbox=None):
    """
    Extracts the 512-D FaceNet embedding vector from BGR image.
    Prioritizes MTCNN for high-quality aligned crops matching registration.
    Falls back to manual bbox crop if MTCNN fails.
    """
    init_models()
    if facenet_model is None or mtcnn is None:
        return None

    try:
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        
        # Try MTCNN first for aligned crop (matches registration exactly)
        face_tensor = mtcnn(img_rgb)
        
        # If MTCNN fails to find a face, fall back to Haar/manual bbox crop
        if face_tensor is None and bbox is not None:
            x1, y1, x2, y2 = [int(val) for val in bbox]
            h, w, _ = img_rgb.shape
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            
            if x2 - x1 >= 10 and y2 - y1 >= 10:
                face_img = img_rgb[y1:y2, x1:x2]
                face_img = cv2.resize(face_img, (160, 160))
                face_tensor = torch.tensor(face_img, dtype=torch.float32).permute(2, 0, 1)
                face_tensor = (face_tensor - 127.5) / 128.0
                
        if face_tensor is None:
            return None
            
        face_tensor = face_tensor.unsqueeze(0).to(device)
        with torch.no_grad():
            emb = facenet_model(face_tensor)
            return emb.squeeze(0).cpu().numpy()
            
    except Exception as e:
        print(f"[ERROR] get_embedding failed: {e}")
        return None

def train_lbph_model():
    """
    Queries all registered students from Django db, extracts face crops
    from their profile photos, and trains the backup LBPH model.
    """
    from attendance.models import Student # Import inside function to avoid circular imports
    
    students = Student.objects.all()
    if not students.exists():
        print("[WARNING] LBPH Train: No students in database.")
        return False

    faces = []
    labels = []
    # label_index -> student_id mapping
    labels_map = {}
    
    print("[INFO] Starting LBPH Fallback training on Django Students...")
    
    for idx, s in enumerate(students):
        if not s.photo:
            continue
            
        photo_path = s.photo.path
        if not os.path.exists(photo_path):
            continue
            
        img = cv2.imread(photo_path)
        if img is None:
            continue
            
        # Detect face using Haar Cascade
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces_detected = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5)
        
        face_crop = None
        if len(faces_detected) > 0:
            x, y, w, h = faces_detected[0]
            face_crop = gray[y:y+h, x:x+w]
        else:
            # If Haar fails, fallback to MTCNN bounding boxes
            init_models()
            if mtcnn is not None:
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                boxes, _ = mtcnn.detect(img_rgb)
                if boxes is not None and len(boxes) > 0:
                    x1, y1, x2, y2 = [int(val) for val in boxes[0]]
                    bh, bw, _ = img.shape
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(bw, x2), min(bh, y2)
                    if x2 - x1 > 10 and y2 - y1 > 10:
                        face_crop = gray[y1:y2, x1:x2]
                        
        if face_crop is not None:
            face_resized = cv2.resize(face_crop, (150, 150))
            faces.append(face_resized)
            labels.append(idx)
            labels_map[idx] = {
                'id': s.id,
                'name': s.name,
                'roll_number': s.roll_number,
                'department': s.department,
                'section': s.section
            }
            
    if len(faces) == 0:
        print("[WARNING] LBPH Train: No face crops extracted from student profile photos.")
        return False
        
    try:
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.train(faces, np.array(labels, dtype=np.int32))
        recognizer.save(LBPH_YML_PATH)
        
        with open(LBPH_LABELS_PATH, "wb") as f:
            pickle.dump(labels_map, f)
            
        print(f"[SUCCESS] Trained LBPH model with {len(faces)} student face profiles.")
        return True
    except Exception as e:
        print(f"[ERROR] LBPH Model training failed: {e}")
        return False

def predict_lbph(gray_crop):
    """
    Predicts identity from grayscale face crop using trained LBPH model.
    Returns (student_id, student_name, roll_number, confidence) or (None, None, None, None).
    """
    if not os.path.exists(LBPH_YML_PATH) or not os.path.exists(LBPH_LABELS_PATH):
        return None, None, None, None
        
    try:
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.read(LBPH_YML_PATH)
        
        label_idx, confidence_score = recognizer.predict(gray_crop)
        
        # Load labels map
        with open(LBPH_LABELS_PATH, "rb") as f:
            labels_map = pickle.load(f)
            
        if label_idx in labels_map and confidence_score < 75: # Threshold check
            student_info = labels_map[label_idx]
            # Convert confidence distance to a percentage
            conf_pct = (1.0 - (confidence_score / 75.0)) * 100
            conf_pct = max(0.0, min(100.0, conf_pct))
            return (
                student_info['id'], 
                student_info['name'], 
                student_info.get('roll_number', '-'), 
                conf_pct
            )
            
    except Exception as e:
        print(f"[ERROR] LBPH prediction failed: {e}")
        
    return None, None, None, None
