from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from pymongo import MongoClient
from datetime import datetime
import markdown
import re
import time
import os

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET')
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

client = OpenAI(api_key=(os.getenv('OPENAI_SECRET')))

# MongoDB configuration
mongo_client = MongoClient(os.getenv('MDB_URI'), connectTimeoutMS=30000, socketTimeoutMS=None, connect=False, maxPoolsize=1)
db = mongo_client['chat_db']
chats_collection = db['chats']
users_db=mongo_client['users_db']
users_collection = users_db['users']
analytics_db = mongo_client['analytics_db']

class User(UserMixin):
    def __init__(self, username):
        self.id = username

@login_manager.user_loader
def load_user(username):
    user = users_collection.find_one({'username': username})
    if user:
        return User(username)
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Check if the username already exists
        existing_user = users_collection.find_one({'username': username})
        if existing_user:
            flash('Username already exists. Please choose a different username.')
            return redirect(url_for('register'))

        # Hash the password before storing it
        hashed_password = generate_password_hash(password, method='pbkdf2')

        # Create a new user document
        new_user = {
            'username': username,
            'password': hashed_password
        }

        # Insert the new user into the users collection
        users_collection.insert_one(new_user)

        flash('Registration successful. Please log in.')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/terms', methods=['GET', 'POST'])
@login_required
def terms():
    if current_user.id == 'hr':
        return redirect(url_for('dashboard'))  # Redirect 'hr' user to the dashboard page

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'accept':
            return redirect(url_for('chat'))
        elif action == 'go_back':
            logout_user()
            return redirect(url_for('login'))
    return render_template('terms.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Find the user in the users collection
        user = users_collection.find_one({'username': username})

        if user and check_password_hash(user['password'], password):
            user_obj = User(user['username'])
            login_user(user_obj)
            if username == 'hr':
                return redirect(url_for('dashboard'))
            else:
                return redirect(url_for('terms'))
        else:
            flash('Invalid credentials')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Retrieve the latest assistant_response from the 'compliance_results' collection
    latest_result = analytics_db['compliance_results'].find_one(sort=[('timestamp', -1)])

    if latest_result:
        timestamp = latest_result['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
        assistant_response = latest_result['assistant_response']
        # Remove the 'source' strings using regular expressions
        assistant_response_cleaned = re.sub(r'【\d+:\d+†source】', '', assistant_response)
        # Convert the markdown text to HTML
        assistant_response_html = markdown.markdown(assistant_response_cleaned)
    else:
        assistant_response_html = None
        timestamp = None

    return render_template('dashboard.html', assistant_response_html=assistant_response_html, timestamp=timestamp)

@app.route('/chat', methods=['GET', 'POST'])
@login_required
def chat():
    if request.method == 'GET':
        # Fetch the latest conversation for the current user from the database
        latest_conversation = chats_collection.find_one(
            {'user': current_user.id},
            sort=[('timestamp', -1)]
        )

        conversation_ended = False
        chat_history = []

        if latest_conversation and 'thread_id' in latest_conversation:
            # Retrieve messages from the Thread using the saved thread_id
            thread_id = latest_conversation['thread_id']
            messages = client.beta.threads.messages.list(thread_id=thread_id, order="asc")

            for message in messages.data:
                if message.role == 'user':
                    chat_history.append({
                        'user_input': message.content[0].text.value,
                        'chatbot_response': ''
                    })
                elif message.role == 'assistant':
                    chat_history[-1]['chatbot_response'] = message.content[0].text.value

                    # Check if the conversation has ended
                    if 'Take care.' in message.content[0].text.value:
                        conversation_ended = True

        return render_template('chat.html', chat_history=chat_history, conversation_ended=conversation_ended)

    if request.method == 'POST':
        user_input = request.form['message']

        # Create a new Thread if no thread_id is found in the latest conversation
        latest_conversation = chats_collection.find_one(
            {'user': current_user.id},
            sort=[('timestamp', -1)]
        )

        if latest_conversation and 'thread_id' in latest_conversation:
            thread_id = latest_conversation['thread_id']
        else:
            thread = client.beta.threads.create()
            thread_id = thread.id

        # Create a new Message in the Thread
        message = client.beta.threads.messages.create(
            thread_id=thread_id, role="user", content=user_input
        )

        # Create a Run and wait for completion
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_ALpb6Nf94SVBfwG0cKbvXKRe',
        )

        while run.status == "queued" or run.status == "in_progress":
            run = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id,
            )
            time.sleep(0.5)

        # Retrieve the Assistant's response from the Thread's messages
        messages = client.beta.threads.messages.list(thread_id=thread_id, order="asc")
        assistant_response = messages.data[-1].content[0].text.value

        # Save the conversation in the database
        chat_entry = {
            'user': current_user.id,
            'timestamp': datetime.now(),
            'user_input': user_input,
            'chatbot_response': assistant_response,
            'thread_id': thread_id
        }
        chats_collection.insert_one(chat_entry)

        if 'Take care.' in assistant_response:
            # Set a flag in the response indicating that the conversation has ended
            return jsonify({"chatbot_response": assistant_response, "conversation_ended": True})
        else:
            return jsonify({"chatbot_response": assistant_response, "conversation_ended": False})

    return render_template('chat.html', chat_history=chat_history)

@app.route('/new_conversation', methods=['POST'])
@login_required
def new_conversation():
    # Create a new Thread
    thread = client.beta.threads.create()
    thread_id = thread.id

    # Save the new conversation in the database
    chat_entry = {
        'user': current_user.id,
        'timestamp': datetime.now(),
        'thread_id': thread_id
    }
    chats_collection.insert_one(chat_entry)

    return redirect(url_for('chat'))

@app.route('/chat_history', methods=['GET'])
@login_required
def chat_history():
    chat_history = list(chats_collection.find({'user': current_user.id}).sort('timestamp'))
    return jsonify({'chat_history': chat_history})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
