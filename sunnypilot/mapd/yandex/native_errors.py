"""Small shared exception types; safe to import in the connectivity child."""


class AcquisitionCancelled(RuntimeError):
  pass


class NativeTransportError(RuntimeError):
  def __init__(self, category, status=None):
    self.category, self.status = category, status
    super().__init__(category)  # Never export URLs, server text or credentials.
