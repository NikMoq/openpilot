// Executes the production native bridge with real Cereal/msgq and mock transport.
// No Panda USB/SPI constructor or hardware operation is invoked.
#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include <filesystem>
#include <memory>
#include <string>
#include <unistd.h>

#include "cereal/messaging/messaging.h"
#include "selfdrive/pandad/panda.h"

void process_icbm_command(std::vector<Panda *> &, SubMaster *, PubMaster *, bool, bool);

class BridgeComms final : public PandaCommsHandle {
public:
  BridgeComms() : PandaCommsHandle("") {}
  std::array<unsigned char, 8> reply{1, 0, 0, 0, 0, 0, 0, 0};
  bool fail_read = false;
  int writes = 0, reads = 0;
  uint16_t last_id = 0;
  uint8_t last_button = 0;
  void cleanup() override {}
  int control_write(uint8_t request, uint16_t id, uint16_t button, unsigned int timeout) override {
    assert(request == 0xa9 && timeout == 50);
    ++writes;
    last_id = id;
    last_button = button;
    reply[1] = 1;
    reply[2] = button;
    reply[4] = id & 255;
    reply[5] = id >> 8;
    return 0;
  }
  int control_read(uint8_t request, uint16_t p1, uint16_t p2, unsigned char *data,
                   uint16_t length, unsigned int timeout) override {
    assert(request == 0xaa && p1 == 0 && p2 == 0 && length == 8 && timeout == 50);
    ++reads;
    if (fail_read) return -1;
    std::memcpy(data, reply.data(), reply.size());
    return 8;
  }
  int bulk_write(unsigned char, unsigned char *, int, unsigned int) override { assert(false); return -1; }
  int bulk_read(unsigned char, unsigned char *, int, unsigned int) override { assert(false); return -1; }
};

class BridgePanda final : public Panda {
public:
  explicit BridgePanda(std::unique_ptr<PandaCommsHandle> comms) : Panda(0U, std::move(comms)) {}
};

int main() {
  // Native msgq namespaces are local to this disposable process/container run.
  const auto prefix = std::string("icbm-native-bridge-") + std::to_string(getpid());
  assert(setenv("OPENPILOT_PREFIX", prefix.c_str(), 1) == 0);
  assert(unsetenv("ZMQ") == 0);
  assert(std::filesystem::create_directory(std::string("/dev/shm/") + prefix));
  PubMaster commands({"customReserved10"}), status_publisher({"customReserved11"});
  SubMaster command_reader({"customReserved10"}), statuses({"customReserved11"});
  auto comms = std::make_unique<BridgeComms>();
  auto *transport = comms.get();
  BridgePanda panda(std::move(comms));
  auto comms2 = std::make_unique<BridgeComms>();
  auto *transport2 = comms2.get();
  BridgePanda panda2(std::move(comms2));
  std::vector<Panda *> one{&panda}, many{&panda, &panda2};

  auto command = [&](uint16_t id, uint8_t button, bool valid, uint64_t timestamp, bool event_valid = true) {
    MessageBuilder message;
    auto event = message.initEvent();
    event.setLogMonoTime(timestamp);
    event.setValid(event_valid);
    auto out = event.initCustomReserved10();
    out.setRequestId(id);
    out.setButton(button);
    out.setValid(valid);
    assert(commands.send("customReserved10", message) >= 0);
    command_reader.update(1000);
    assert(command_reader.updated("customReserved10"));
  };
  auto process = [&](std::vector<Panda *> &pandas, bool paired, bool onroad) {
    process_icbm_command(pandas, &command_reader, &status_publisher, paired, onroad);
    statuses.update(1000);
    assert(statuses.updated("customReserved11"));
  };
  auto status = [&]() { return statuses["customReserved11"].getCustomReserved11(); };

  command(1, 3, true, nanos_since_boot());
  process(one, true, true);
  assert(transport->writes == 1 && transport->last_id == 1 && transport->last_button == 3);
  assert(status().getSignatureMatched() && status().getProtocolValid());
  assert(status().getRequestId() == 1 && status().getState() == 1);
  process(one, true, true);
  assert(transport->writes == 1);  // Same ID cannot be written twice.

  // Complete that request, then use a distinct next ID for inhibit cases.
  transport->reply[1] = 3;
  transport->reply[6] = 7;
  command(1, 3, true, nanos_since_boot());
  process(one, true, true);
  const uint64_t terminal_time = status().getStatusMonoTimeNs();
  assert(terminal_time > 0 && status().getState() == 3);
  transport->fail_read = true;
  process(one, true, true);
  assert(!status().getProtocolValid() && status().getStatusMonoTimeNs() == 0);
  transport->fail_read = false;
  process(one, true, true);
  assert(status().getStatusMonoTimeNs() == terminal_time);
  assert(transport->writes == 1);

  command(2, 4, true, nanos_since_boot() - 1000000000ULL);
  process(one, true, true);
  assert(transport->writes == 1);
  command(2, 4, true, nanos_since_boot(), false);
  process(one, true, true);
  assert(transport->writes == 1);
  command(2, 4, false, nanos_since_boot());
  process(one, true, true);
  assert(transport->writes == 1);
  command(2, 4, true, nanos_since_boot());
  process(one, true, false);
  assert(transport->writes == 1);
  const int reads = transport->reads;
  process(one, false, true);
  assert(transport->writes == 1 && transport->reads == reads);
  assert(!status().getSignatureMatched() && !status().getProtocolValid());
  process(many, true, true);
  assert(transport->writes == 1 && transport2->writes == 0);
  assert(transport->reads == reads && transport2->reads == 0);
  assert(status().getPandaCount() == 2 && !status().getProtocolValid());
  command(0, 4, true, nanos_since_boot());
  process(one, true, true);
  assert(transport->writes == 1);
  command(2, 5, true, nanos_since_boot());
  process(one, true, true);
  assert(transport->writes == 1);
  command(2, 4, true, nanos_since_boot() + 1000000000ULL);
  process(one, true, true);
  assert(transport->writes == 1);

  // A genuinely new state transition must get a new timestamp.
  command(2, 4, true, nanos_since_boot());
  process(one, true, true);
  assert(transport->writes == 2 && transport->last_id == 2 && transport->last_button == 4);
  assert(status().getStatusMonoTimeNs() > terminal_time);
  std::cout << "ICBM production native bridge: PASS (real msgq/Cereal, no hardware)\n";
}
