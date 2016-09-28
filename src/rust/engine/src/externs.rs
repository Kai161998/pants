use libc;

use std::cell::RefCell;
use std::collections::HashMap;
use std::mem;

use core::{Field, Id, Key, TypeId, Value};

// An opaque pointer to a context used by the extern functions.
pub type ExternContext = libc::c_void;

pub type IsSubClassExtern =
  extern "C" fn(*const ExternContext, *const TypeId, *const TypeId) -> bool;

#[derive(Clone)]
pub struct Externs {
  context: *const ExternContext,
  key_for: KeyForExtern,
  val_for: ValForExtern,
  issubclass: IsSubClassExtern,
  issubclass_cache: RefCell<HashMap<(TypeId,TypeId),bool>>,
  store_list: StoreListExtern,
  project: ProjectExtern,
  project_multi: ProjectMultiExtern,
  id_to_str: IdToStrExtern,
  val_to_str: ValToStrExtern,
}

impl Externs {
  pub fn new(
    ext_context: *const ExternContext,
    key_for: KeyForExtern,
    val_for: ValForExtern,
    id_to_str: IdToStrExtern,
    val_to_str: ValToStrExtern,
    issubclass: IsSubClassExtern,
    store_list: StoreListExtern,
    project: ProjectExtern,
    project_multi: ProjectMultiExtern,
  ) -> Externs {
    Externs {
      context: ext_context,
      key_for: key_for,
      val_for: val_for,
      issubclass: issubclass,
      issubclass_cache: RefCell::new(HashMap::new()),
      store_list: store_list,
      project: project,
      project_multi: project_multi,
      id_to_str: id_to_str,
      val_to_str: val_to_str,
    }
  }

  pub fn key_for(&self, val: &Value) -> Key {
    (self.key_for)(self.context, val)
  }

  pub fn val_for(&self, key: &Key) -> Value {
    (self.val_for)(self.context, key)
  }

  pub fn issubclass(&self, cls: &TypeId, super_cls: &TypeId) -> bool {
    if cls == super_cls {
      true
    } else {
      self.issubclass_cache.borrow_mut().entry((*cls, *super_cls))
        .or_insert_with(||
          (self.issubclass)(self.context, cls, super_cls)
        )
        .clone()
    }
  }

  pub fn store_list(&self, values: Vec<&Value>, merge: bool) -> Value {
    let values_clone: Vec<Value> = values.into_iter().map(|&v| v).collect();
    (self.store_list)(self.context, values_clone.as_ptr(), values_clone.len() as u64, merge)
  }

  pub fn project(&self, value: &Value, field: &Field, type_id: &TypeId) -> Value {
    (self.project)(self.context, value, field, type_id)
  }

  pub fn project_multi(&self, value: &Value, field: &Field) -> Vec<Value> {
    let buf = (self.project_multi)(self.context, value, field);
    with_vec(buf.values_ptr, buf.values_len as usize, |value_vec| value_vec.clone())
  }

  pub fn id_to_str(&self, digest: &Id) -> String {
    let buf = (self.id_to_str)(self.context, digest);
    let str =
      with_vec(buf.str_ptr, buf.str_len as usize, |char_vec| {
        // Attempt to decode from unicode.
        String::from_utf8(char_vec.clone()).unwrap_or_else(|e| {
          format!("<failed to decode unicode for {:?}: {}>", digest, e)
        })
      });
    str
  }

  pub fn val_to_str(&self, val: &Value) -> String {
    let buf = (self.val_to_str)(self.context, val);
    let str =
      with_vec(buf.str_ptr, buf.str_len as usize, |char_vec| {
        // Attempt to decode from unicode.
        String::from_utf8(char_vec.clone()).unwrap_or_else(|e| {
          format!("<failed to decode unicode for {:?}: {}>", val, e)
        })
      });
    str
  }
}

pub type KeyForExtern =
  extern "C" fn(*const ExternContext, *const Value) -> Key;

pub type ValForExtern =
  extern "C" fn(*const ExternContext, *const Key) -> Value;

pub type StoreListExtern =
  extern "C" fn(*const ExternContext, *const Value, u64, bool) -> Value;

pub type ProjectExtern =
  extern "C" fn(*const ExternContext, *const Value, *const Field, *const TypeId) -> Value;

#[repr(C)]
pub struct ValueBuffer {
  values_ptr: *mut Value,
  values_len: u64,
}

pub type ProjectMultiExtern =
  extern "C" fn(*const ExternContext, *const Value, *const Field) -> ValueBuffer;

#[repr(C)]
pub struct UTF8Buffer {
  str_ptr: *mut u8,
  str_len: u64,
}

pub type IdToStrExtern =
  extern "C" fn(*const ExternContext, *const Id) -> UTF8Buffer;

pub type ValToStrExtern =
  extern "C" fn(*const ExternContext, *const Value) -> UTF8Buffer;

pub fn with_vec<F,C,T>(c_ptr: *mut C, c_len: usize, f: F) -> T
    where F: FnOnce(&Vec<C>)->T {
  let cs = unsafe { Vec::from_raw_parts(c_ptr, c_len, c_len) };
  let output = f(&cs);
  mem::forget(cs);
  output
}
