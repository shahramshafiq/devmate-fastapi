class SessionService:

    def __init__(self):
        self.sessions = {}

    def get_session(self, user_id: str, session_id: str):

        key = f"{user_id}:{session_id}"

        if key not in self.sessions:
            self.sessions[key] = {
                "session_input_tokens": 0,
                "session_output_tokens": 0,
                "session_cost": 0.0
            }

        return self.sessions[key]

    def update_session(
        self,
        user_id: str,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        cost: float
    ):

        session = self.get_session(user_id, session_id)

        session["session_input_tokens"] += input_tokens
        session["session_output_tokens"] += output_tokens
        session["session_cost"] += cost

        return session