from enum import Enum
from abc import ABC, abstractmethod

class TxStatus(int, Enum):
    """
    handler results for indicating if state should change
    """
    ERROR           = -1
    IGNORED         =  0
    INIT            =  1
    HANDLED         =  2
    TRANSITIONED    =  3
    COMPLETE        =  4

class TxStateHandler(ABC):
    """abstract base class for protocol state handlers"""

    @classmethod
    @abstractmethod
    async def entry(cls, context) -> TxStatus:
        return TxStatus.IGNORED

    @classmethod
    @abstractmethod
    async def handle(cls, context) -> TxStatus:
        return TxStatus.IGNORED

    # exit should not perform significant state modifications
    # and should not be async in order to avoid awkwardness about handling responses
    @classmethod
    @abstractmethod
    def exit(cls, context) -> TxStatus:
        return TxStatus.IGNORED

class BaseDFUManager(ABC):

    @abstractmethod
    async def run(self):
        raise NotImplementedError("a DFUManager class must implement `run()`")