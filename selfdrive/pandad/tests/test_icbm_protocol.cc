#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include "selfdrive/pandad/panda.h"

class MockComms final : public PandaCommsHandle {
public:
  MockComms() : PandaCommsHandle("") {}
  std::array<unsigned char, 8> reply{1, 7, 4, 2, 0x34, 0x12, 0xcd, 0xab};
  int reply_size = 8, write_result = 0, writes = 0;
  uint8_t request = 0;
  uint16_t param1 = 0, param2 = 0;
  unsigned int timeout = 0;
  void cleanup() override {}
  int control_write(uint8_t req, uint16_t p1, uint16_t p2, unsigned int t) override {
    ++writes; request = req; param1 = p1; param2 = p2; timeout = t;
    return write_result;
  }
  int control_read(uint8_t req, uint16_t p1, uint16_t p2, unsigned char *data,
                   uint16_t length, unsigned int t) override {
    assert(req == 0xaa && p1 == 0 && p2 == 0 && length == 8 && t == 50);
    std::memcpy(data, reply.data(), reply.size());
    return reply_size;
  }
  int bulk_write(unsigned char, unsigned char *, int, unsigned int) override { assert(false); return -1; }
  int bulk_read(unsigned char, unsigned char *, int, unsigned int) override { assert(false); return -1; }
};

class TestPanda final : public Panda {
public:
  explicit TestPanda(std::unique_ptr<PandaCommsHandle> mock) : Panda(0U, std::move(mock)) {}
};

int main() {
  auto mock = std::make_unique<MockComms>();
  auto *transport = mock.get();
  TestPanda panda(std::move(mock));
  assert(!panda.icbm_button_command(0, 1));
  assert(!panda.icbm_button_command(1, 0));
  assert(!panda.icbm_button_command(1, 5));
  assert(transport->writes == 0);
  for (uint8_t button = 1; button <= 4; ++button) {
    assert(panda.icbm_button_command(65535, button));
    assert(transport->request == 0xa9 && transport->param1 == 65535);
    assert(transport->param2 == button && transport->timeout == 50);
  }
  transport->write_result = -7;
  assert(!panda.icbm_button_command(1, 1));
  auto status = panda.icbm_button_status();
  assert(status && status->version == 1 && status->state == 7 && status->button == 4);
  assert(status->reason == 2 && status->request_id == 0x1234 && status->tx_counter == 0xabcd);
  for (int size : {-7, 0, 7, 9}) {
    transport->reply_size = size;
    assert(!panda.icbm_button_status());
  }
  transport->reply_size = 8;
  transport->reply[0] = 2;
  assert(!panda.icbm_button_status());
  transport->reply[0] = 1;
  transport->reply[1] = 8;
  assert(!panda.icbm_button_status());
  transport->reply[1] = 7;
  transport->reply[2] = 5;
  assert(!panda.icbm_button_status());
  for (uint8_t state = 0; state <= 7; ++state) {
    transport->reply[1] = state;
    transport->reply[2] = 0;
    assert(panda.icbm_button_status()->state == state);
  }
  std::cout << "ICBM native protocol: PASS (no hardware)\n";
}
