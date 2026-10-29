"""Run ubloxd's actual receive loop against isolated native msgq sockets."""
import ast
import _thread
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid

import cereal.messaging as messaging
import pytest


class Finished(Exception):
  pass


def main_method(transport, seen):
  source = Path(__file__).resolve().parents[2] / 'system/ubloxd/ubloxd.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
  def add_data(stamp, raw):
    seen.append(raw)
    return []
  parser = SimpleNamespace(framer=SimpleNamespace(add_data=add_data))
  namespace = {'messaging': transport, 'UbloxMsgParser': lambda: parser}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
  return namespace['main']


def transport(endpoint, stop_after=None):
  reads = []
  sockets = []
  def sub_sock(service, **kwargs):
    assert service == 'ubloxRaw' and kwargs == {'timeout': 100, 'conflate': False}
    socket = messaging.sub_sock(endpoint, **kwargs)
    sockets.append(socket)
    return socket
  def receive(blocking, socket):
    if stop_after is not None and len(reads) >= stop_after:
      raise Finished()
    value = messaging.recv_one(socket) if blocking else messaging.recv_one_or_none(socket)
    reads.append(value)
    return value
  proxy = SimpleNamespace(sub_sock=sub_sock, PubMaster=lambda services: SimpleNamespace(send=lambda *args: None),
                          recv_one=lambda sock: receive(True, sock),
                          recv_one_or_none=lambda sock: receive(False, sock))
  return proxy, reads, sockets


def endpoint():
  # Native msgq endpoints are independent from the envelope's service name.
  return 'ubloxd_idle_test_' + uuid.uuid4().hex


def test_empty_queue_waits_for_existing_timeout_without_consuming_a_core():
  proxy, reads, sockets = transport(endpoint(), stop_after=3)
  wall, cpu = time.monotonic(), time.process_time()
  with pytest.raises(Finished):
    main_method(proxy, [])()
  wall, cpu = time.monotonic() - wall, time.process_time() - cpu
  assert reads == [None, None, None]
  assert wall >= .24  # Three real 100 ms timeouts, not three nonblocking polls.
  assert cpu < wall * .5


def test_arrival_wakes_receive_and_nonconflated_burst_is_not_lost():
  name = endpoint()
  publisher = messaging.pub_sock(name)
  proxy, reads, sockets = transport(name, stop_after=20)
  seen = []
  payloads = [bytes([number]) for number in range(20)]
  ready = threading.Event()
  def send():
    ready.wait(2)
    time.sleep(.02)
    for raw in payloads:
      event = messaging.new_message('ubloxRaw', len(raw), valid=True)
      event.ubloxRaw = raw
      publisher.send(event.to_bytes())
  original = proxy.sub_sock
  def sub_sock(*args, **kwargs):
    sock = original(*args, **kwargs)
    ready.set()
    return sock
  proxy.sub_sock = sub_sock
  sender = threading.Thread(target=send)
  sender.start()
  start = time.monotonic()
  try:
    with pytest.raises(Finished):
      main_method(proxy, seen)()
  finally:
    sender.join(timeout=2)
  assert seen == payloads
  assert time.monotonic() - start < 1
  assert all(item is not None for item in reads)


def test_keyboard_shutdown_is_observed_within_bounded_empty_receive():
  proxy, _, sockets = transport(endpoint())
  timer = threading.Timer(.02, _thread.interrupt_main)
  start = time.monotonic()
  timer.start()
  try:
    with pytest.raises(KeyboardInterrupt):
      main_method(proxy, [])()
  finally:
    timer.cancel()
    timer.join()
  assert time.monotonic() - start < .5
