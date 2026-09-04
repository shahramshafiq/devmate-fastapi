from pydantic import BaseModel, Field, field_validator

class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=500, gt=0, le=4096)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message cannot be empty or whitespace only")
        return v

class Turn(BaseModel):
    turn_input_tokens: int
    turn_output_tokens: int
    turn_cost: float

class Session(BaseModel):    
    session_input_tokens: int
    session_output_tokens: int
    session_cost: float

class ChatResponse(BaseModel):
    reply:str
    turn: Turn
    session: Session