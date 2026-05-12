import os
import stat
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Security: Ensure these files are kept private and never committed to version control.
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"

# Scope for YouTube Data API to allow uploading videos
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

def get_authenticated_service():
    """
    Authenticates the user and returns an authorized YouTube API service object.
    Enforces strict user verification via OAuth2.
    """
    credentials = None
    
    # Check if we already have valid credentials stored
    if os.path.exists(TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        
    # If there are no valid credentials available, require the user to log in.
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(f"Missing {CLIENT_SECRETS_FILE}. Please download it from Google Cloud Console.")
                
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        with open(TOKEN_FILE, 'w') as token_file:
            token_file.write(credentials.to_json())
            
        # Security Rule: Enforce strict permissions on the token file to prevent unauthorized access
        os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)
            
    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

def upload_video(youtube, file_path, title, description, category_id, privacy_status):
    """
    Uploads a video to YouTube with the specified metadata.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"The video file '{file_path}' was not found.")

    print(f"Preparing to upload '{file_path}'...")
    
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'categoryId': category_id
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False
        }
    }

    # Call the API's videos.insert method to create and upload the video.
    insert_request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=MediaFileUpload(file_path, chunksize=-1, resumable=True)
    )

    print("Uploading video... This may take a while depending on your connection.")
    response = insert_request.execute()

    print("\n--- Upload Successful ---")
    print(f"Video ID: {response['id']}")
    print(f"URL: https://youtu.be/{response['id']}")
    print("-------------------------")

if __name__ == '__main__':
    try:
        # Authenticate and construct the service
        youtube_service = get_authenticated_service()
        
        # Define video metadata
        video_file = "final_short.mp4"
        video_title = "My Awesome Cartoon Trivia #Shorts"
        video_description = "Check out this amazing cartoon trivia! Like and subscribe for more."
        
        # YouTube Category ID '1' corresponds to 'Film & Animation'
        category_id = "1"
        
        # Privacy status requested as 'private'
        privacy_status = "private"
        
        # Execute the upload
        upload_video(
            youtube=youtube_service,
            file_path=video_file,
            title=video_title,
            description=video_description,
            category_id=category_id,
            privacy_status=privacy_status
        )
        
    except Exception as e:
        print(f"\nAn error occurred during the upload process:\n{e}")
