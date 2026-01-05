// static/js/script.js - Updated for Black & White Minimalist Design

// Black & White SVG Icons
const closeButton = `<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">
<path fill="#000000" d="M15.884 4.116l-1.768-1.768-5.884 5.884-5.884-5.884-1.768 1.768 5.884 5.884-5.884 5.884 1.768 1.768 5.884-5.884 5.884 5.884 1.768-1.768-5.884-5.884 5.884-5.884z"></path>
</svg>`;

const menuButton = `<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">
<circle fill="#000000" cx="10" cy="3" r="2"/>
<circle fill="#000000" cx="10" cy="10" r="2"/>
<circle fill="#000000" cx="10" cy="17" r="2"/>
</svg>`;

// Action Buttons (Edit/Delete)
const actionButtons = document.querySelectorAll('.action-button');

if (actionButtons) {
  actionButtons.forEach(button => {
    button.addEventListener('click', (e) => {
      e.stopPropagation();
      const buttonId = button.dataset.id;
      let popup = document.querySelector(`.popup-${buttonId}`);
      
      // Remove existing popup if clicked again
      if (popup) {
        button.innerHTML = menuButton;
        popup.remove();
        return;
      }

      // Get URLs from data attributes
      const deleteUrl = button.dataset.deleteUrl;
      const editUrl = button.dataset.editUrl;
      
      // Change button icon to close
      button.innerHTML = closeButton;

      // Create popup menu
      popup = document.createElement('div');
      popup.classList.add('popup', `popup-${buttonId}`);
      
      // Black & White popup styling
      popup.innerHTML = `
        <div class="popup-content">
          <a href="${editUrl}" class="popup-edit">
            <svg width="16" height="16" viewBox="0 0 16 16">
              <path fill="#000000" d="M12.146 0.146l3.146 3.146-12 12h-3.146v-3.146l12-12zM1.854 13.854l-1.5-1.5v1.5h1.5zM15.146 3.146l-1.5-1.5 1.5 1.5z"/>
            </svg>
            Edit
          </a>
          <form action="${deleteUrl}" method="POST" class="popup-delete">
            {% csrf_token %}
            <button type="submit" class="delete-btn">
              <svg width="16" height="16" viewBox="0 0 16 16">
                <path fill="#000000" d="M3 14c0 1.1 0.9 2 2 2h6c1.1 0 2-0.9 2-2v-10h-10v10zM5 6h6v8h-6v-8zM10.5 1l-1-1h-3l-1 1h-2v2h10v-2h-2z"/>
              </svg>
              Delete
            </button>
          </form>
        </div>
      `;
      
      // Insert popup after button
      button.insertAdjacentElement('afterend', popup);
    });
  });
  
  // Close popups when clicking elsewhere
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.action-button') && !e.target.closest('.popup')) {
      document.querySelectorAll('.popup').forEach(popup => popup.remove());
      actionButtons.forEach(button => {
        button.innerHTML = menuButton;
      });
    }
  });
}

// Dropdown Menu
const dropdownMenu = document.querySelector(".dropdown-menu");
const dropdownButton = document.querySelector(".dropdown-button");

if (dropdownButton) {
  dropdownButton.addEventListener("click", (e) => {
    e.stopPropagation();
    dropdownMenu.classList.toggle("show");
  });
  
  // Close dropdown when clicking elsewhere
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.dropdown-button') && !e.target.closest('.dropdown-menu')) {
      dropdownMenu.classList.remove('show');
    }
  });
}

// Avatar Preview
const photoInput = document.querySelector("#avatar");
const photoPreview = document.querySelector("#preview-avatar");

if (photoInput && photoPreview) {
  photoInput.onchange = () => {
    const [file] = photoInput.files;
    if (file) {
      photoPreview.src = URL.createObjectURL(file);
    }
  };
}

// Auto-scroll to bottom of conversation
const conversationThread = document.querySelector(".room__box");
if (conversationThread) {
  conversationThread.scrollTop = conversationThread.scrollHeight;
  
  // Also auto-scroll when new messages are added
  const observer = new MutationObserver(() => {
    conversationThread.scrollTop = conversationThread.scrollHeight;
  });
  
  observer.observe(conversationThread, { childList: true, subtree: true });
}

// Form Validation Enhancements
document.addEventListener('DOMContentLoaded', function() {
  // Add focus styles to form inputs
  const formInputs = document.querySelectorAll('input[type="text"], input[type="password"], input[type="email"], textarea');
  formInputs.forEach(input => {
    input.addEventListener('focus', function() {
      this.parentElement.classList.add('focused');
    });
    
    input.addEventListener('blur', function() {
      this.parentElement.classList.remove('focused');
    });
  });
  
  // Password visibility toggle
  const passwordToggles = document.querySelectorAll('.password-toggle');
  passwordToggles.forEach(toggle => {
    toggle.addEventListener('click', function() {
      const input = this.previousElementSibling;
      const type = input.getAttribute('type') === 'password' ? 'text' : 'password';
      input.setAttribute('type', type);
      
      // Toggle eye icon
      this.innerHTML = type === 'password' ? 
        '<svg width="20" height="20" viewBox="0 0 20 20"><path fill="#000000" d="M10 4c-4.971 0-9 4.029-9 9s4.029 9 9 9 9-4.029 9-9-4.029-9-9-9zM10 18.5c-3.038 0-5.5-2.462-5.5-5.5s2.462-5.5 5.5-5.5 5.5 2.462 5.5 5.5-2.462 5.5-5.5 5.5zM10 8c-1.105 0-2 0.895-2 2s0.895 2 2 2 2-0.895 2-2-0.895-2-2-2z"/></svg>' :
        '<svg width="20" height="20" viewBox="0 0 20 20"><path fill="#000000" d="M10 12c1.105 0 2-0.895 2-2s-0.895-2-2-2-2 0.895-2 2 0.895 2 2 2zM19.228 9.295c-1.841-2.726-4.795-4.295-8.228-4.295s-6.387 1.569-8.228 4.295l-0.772-0.637 0.772 0.637c-0.187 0.277-0.772 1.344-0.772 2.705s0.585 2.428 0.772 2.705c1.841 2.726 4.795 4.295 8.228 4.295s6.387-1.569 8.228-4.295c0.187-0.277 0.772-1.344 0.772-2.705s-0.585-2.428-0.772-2.705zM10 15c-2.757 0-5-2.243-5-5s2.243-5 5-5 5 2.243 5 5-2.243 5-5 5z"/></svg>';
    });
  });
  
  // Mobile menu toggle
  const mobileMenuBtn = document.getElementById('mobileMenuBtn');
  const sidebar = document.getElementById('sidebar');
  
  if (mobileMenuBtn && sidebar) {
    mobileMenuBtn.addEventListener('click', () => {
      sidebar.classList.toggle('active');
    });
  }
});

// Message submission enhancement
const messageForm = document.querySelector('.comment-form form');
if (messageForm) {
  messageForm.addEventListener('submit', function(e) {
    const messageInput = this.querySelector('input[name="body"]');
    if (messageInput && messageInput.value.trim() === '') {
      e.preventDefault();
      messageInput.classList.add('error');
      setTimeout(() => messageInput.classList.remove('error'), 1000);
    }
  });
}

// Add CSS for popups
const style = document.createElement('style');
style.textContent = `
  .popup {
    position: absolute;
    right: 0;
    top: 100%;
    background: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    z-index: 1000;
    min-width: 120px;
    margin-top: 5px;
    animation: fadeIn 0.2s ease-out;
  }
  
  .popup-content {
    display: flex;
    flex-direction: column;
    padding: 8px 0;
  }
  
  .popup-edit,
  .popup-delete button {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    text-decoration: none;
    color: #000000;
    font-size: 1.3rem;
    font-weight: 500;
    background: none;
    border: none;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }
  
  .popup-edit:hover,
  .popup-delete button:hover {
    background-color: #F5F5F5;
  }
  
  .popup-edit svg,
  .popup-delete button svg {
    width: 14px;
    height: 14px;
  }
  
  .popup-delete {
    margin: 0;
  }
  
  .popup-delete button {
    width: 100%;
    text-align: left;
  }
  
  @keyframes fadeIn {
    from {
      opacity: 0;
      transform: translateY(-10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
`;
document.head.appendChild(style);
