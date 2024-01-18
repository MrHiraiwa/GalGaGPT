let userId = window.preloadedUserId; // サーバーサイドから提供されるユーザーID

function getUserIdFromCookie() {
    const cookies = document.cookie.split('; ');
    const userCookie = cookies.find(row => row.startsWith('userId='));
    return userCookie ? userCookie.split('=')[1] : null;
}

function addMessageWithAnimation(chatBox, message, isUser) {
    var messageDiv = document.createElement('div');
    messageDiv.textContent = (isUser ? "You: " : "Bot: ") + message;
    messageDiv.className = 'message-animation'; // アニメーション用のクラスを追加
    chatBox.appendChild(messageDiv);

    // アニメーションが終了したら、メッセージを追加
    messageDiv.addEventListener('animationend', function() {
        messageDiv.classList.remove('message-animation');
    });
}

function sendMessage() {
    var message = document.getElementById("userInput").value;
    if (!message.trim()) {
        return;
    }

    var postData = { message: message };
    if (userId !== null) {
        postData.user_id = userId;
    }

    fetch('/webhook', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(postData)
    })
    .then(response => response.json())
    .then(data => {
        var chatBox = document.getElementById("chatBox");
        addMessageWithAnimation(chatBox, message, true); // ユーザーメッセージ
        addMessageWithAnimation(chatBox, data.reply, false); // ボットメッセージ
        document.getElementById("userInput").value = '';
    });
}

window.onload = function() {
    document.getElementById("chatContainer").style.display = "block";

    document.getElementById("userInput").addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault(); // フォームの自動送信を防止
            sendMessage();
        }
    });
};
