from pydantic import BaseModel, Field
from typing import Optional


class TransactionCreate(BaseModel):
    date: str = Field(..., description="交易日期 YYYY-MM-DD")
    symbol: str = Field(..., description="股票代码/基金名称")
    business_type: str = Field(..., description="买入/卖出/分红入账/股息再投资/其他")
    cash_flow: float = Field(..., description="现金流，买入为负，卖出/分红为正")
    shares: Optional[float] = None
    price: Optional[float] = None
    market_value: Optional[float] = None
    notes: Optional[str] = None
    account_id: int = 1


class TransactionUpdate(BaseModel):
    date: Optional[str] = None
    symbol: Optional[str] = None
    business_type: Optional[str] = None
    cash_flow: Optional[float] = None
    shares: Optional[float] = None
    price: Optional[float] = None
    market_value: Optional[float] = None
    notes: Optional[str] = None
    account_id: Optional[int] = None


class TransactionResponse(BaseModel):
    id: int
    account_id: int
    date: str
    symbol: str
    business_type: str
    cash_flow: float
    shares: Optional[float] = None
    price: Optional[float] = None
    market_value: Optional[float] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
