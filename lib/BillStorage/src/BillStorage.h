#pragma once

class BillStorage {
 public:
  // Mounts the shared bill filesystem without formatting it. The composition
  // root owns this call and passes the result to every GATT component.
  bool begin();
  bool mounted() const { return mounted_; }

 private:
  bool mounted_ = false;
};
