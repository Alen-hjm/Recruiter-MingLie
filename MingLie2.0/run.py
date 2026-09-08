from __future__ import annotations

import os
from app.factory import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("MINGLIE_PORT", "5050")), debug=False)
