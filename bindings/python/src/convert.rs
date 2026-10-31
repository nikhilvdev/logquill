//! Python objects to JSON values and back.
//!
//! `meta` comes from application code, so the conversion treats it as hostile: a structure that
//! contains itself becomes a marker string instead of looping, a nesting deeper than
//! [`MAX_DEPTH`] is cut off, and a value of a type JSON has no name for is written with `str()`
//! (as the old library's `default=str` did). Numbers keep Python's own spelling (`repr` for floats,
//! `str` for integers), because the tamper chain hashes that text and it must not change.

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use serde_json::{Map, Value};

/// What to do with NaN and the infinities, which JSON cannot hold.
#[derive(Clone, Copy)]
pub enum NonFinite {
    /// Write `null` (decision D42): for log output, where a bad number must not lose the record.
    Null,
    /// Raise `ValueError`: for hashing, where changing a value silently would hide an edit.
    Error,
}

/// How deep a structure may nest before the rest is replaced by a marker.
pub const MAX_DEPTH: usize = 100;
const CYCLE: &str = "<circular reference>";
const TOO_DEEP: &str = "<max depth exceeded>";

/// Whether `obj` is plain JSON data that conversion would hand back equal and unchanged: an exact
/// `dict` (every key a `str`), `list`, `str`, `bool`, `None`, an `int` that fits `i64` or a finite
/// `float`, nested at most 32 deep (which also rules out a cycle). Anything else (a tuple, a
/// subclass, a callable, NaN) is `false`, and the caller takes the sanitising path instead. Lets a
/// Python-only transport be handed the caller's own dict when that is indistinguishable from the
/// converted copy.
#[pyfunction]
pub fn is_json_safe(obj: &Bound<'_, PyAny>) -> bool {
    fn walk(obj: &Bound<'_, PyAny>, depth: usize) -> bool {
        if depth > 32 {
            return false;
        }
        if obj.is_none()
            || obj.is_exact_instance_of::<PyBool>()
            || obj.is_exact_instance_of::<PyString>()
        {
            return true;
        }
        if obj.is_exact_instance_of::<PyInt>() {
            return obj.extract::<i64>().is_ok();
        }
        if obj.is_exact_instance_of::<PyFloat>() {
            return obj.extract::<f64>().is_ok_and(f64::is_finite);
        }
        if obj.is_exact_instance_of::<PyList>() {
            return obj
                .cast::<PyList>()
                .is_ok_and(|list| list.iter().all(|item| walk(&item, depth + 1)));
        }
        if obj.is_exact_instance_of::<PyDict>() {
            return obj.cast::<PyDict>().is_ok_and(|dict| {
                dict.iter().all(|(key, value)| {
                    key.is_exact_instance_of::<PyString>() && walk(&value, depth + 1)
                })
            });
        }
        false
    }
    walk(obj, 0)
}

/// The JSON value for a Python object.
pub fn to_value(obj: &Bound<'_, PyAny>, non_finite: NonFinite) -> PyResult<Value> {
    convert(obj, non_finite, &mut Vec::new())
}

fn number_from_text(text: &str) -> PyResult<Value> {
    serde_json::from_str(text)
        .map_err(|e| PyValueError::new_err(format!("not a JSON number: {text} ({e})")))
}

/// Converts one value. On GIL-enabled builds the common types take the fast path in [`fast`];
/// everything else, and every free-threaded build, takes [`convert_generic`].
fn convert(
    obj: &Bound<'_, PyAny>,
    non_finite: NonFinite,
    path: &mut Vec<usize>,
) -> PyResult<Value> {
    #[cfg(not(Py_GIL_DISABLED))]
    {
        fast::convert(obj.py(), obj.as_ptr(), non_finite, path)
    }
    #[cfg(Py_GIL_DISABLED)]
    {
        convert_generic(obj, non_finite, path)
    }
}

fn convert_generic(
    obj: &Bound<'_, PyAny>,
    non_finite: NonFinite,
    path: &mut Vec<usize>,
) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if obj.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(obj.extract::<bool>()?));
    }
    if obj.is_instance_of::<PyInt>() {
        return number_from_text(&obj.str()?.to_string_lossy());
    }
    if obj.is_instance_of::<PyFloat>() {
        let x: f64 = obj.extract()?;
        if !x.is_finite() {
            return match non_finite {
                NonFinite::Null => Ok(Value::Null),
                NonFinite::Error => Err(PyValueError::new_err(
                    "NaN and infinity have no JSON form \u{2014} replace them before hashing",
                )),
            };
        }
        return number_from_text(&obj.repr()?.to_string_lossy());
    }
    if let Ok(text) = obj.cast::<PyString>() {
        return Ok(Value::String(text.to_string_lossy().into_owned()));
    }
    let is_list = obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>();
    let is_dict = obj.is_instance_of::<PyDict>();
    if is_list || is_dict {
        let id = obj.as_ptr() as usize;
        if path.contains(&id) {
            return Ok(Value::String(CYCLE.to_string()));
        }
        if path.len() >= MAX_DEPTH {
            return Ok(Value::String(TOO_DEEP.to_string()));
        }
        path.push(id);
        let result = if is_list {
            let mut items = Vec::new();
            for item in obj.try_iter()? {
                items.push(convert(&item?, non_finite, path)?);
            }
            Ok(Value::Array(items))
        } else {
            let dict = obj.cast::<PyDict>()?;
            let mut map = Map::new();
            for (key, value) in dict.iter() {
                map.insert(key_text(&key)?, convert(&value, non_finite, path)?);
            }
            Ok(Value::Object(map))
        };
        path.pop();
        return result;
    }
    // Anything else (datetime, UUID, a custom object): its text, never an exception.
    Ok(Value::String(match obj.str() {
        Ok(text) => text.to_string_lossy().into_owned(),
        Err(_) => format!("<unprintable {}>", obj.get_type().name()?),
    }))
}

/// A dictionary key as JSON object keys are written: strings as they are, and None, booleans,
/// integers and floats the way `json.dumps` spells them.
fn key_text(key: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(text) = key.cast::<PyString>() {
        return Ok(text.to_string_lossy().into_owned());
    }
    if key.is_none() {
        return Ok("null".to_string());
    }
    if let Ok(flag) = key.cast::<PyBool>() {
        return Ok(if flag.is_true() { "true" } else { "false" }.to_string());
    }
    if key.is_instance_of::<PyInt>() {
        return Ok(key.str()?.to_string_lossy().into_owned());
    }
    if key.is_instance_of::<PyFloat>() {
        return Ok(key.repr()?.to_string_lossy().into_owned());
    }
    Err(PyTypeError::new_err(format!(
        "a dictionary key must be a string, number, boolean or None, not {}",
        key.get_type().name()?
    )))
}

/// The Python object for a JSON value.
pub fn to_py<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        Value::Null => py.None().into_bound(py),
        Value::Bool(flag) => PyBool::new(py, *flag).to_owned().into_any(),
        Value::Number(number) => {
            let text = number.to_string();
            if text.contains(['.', 'e', 'E']) {
                let x: f64 = text
                    .parse()
                    .map_err(|_| PyValueError::new_err(format!("not a number: {text}")))?;
                PyFloat::new(py, x).into_any()
            } else {
                py.get_type::<PyInt>().call1((text,))?
            }
        }
        Value::String(text) => PyString::new(py, text).into_any(),
        Value::Array(items) => {
            let converted = items
                .iter()
                .map(|item| to_py(py, item))
                .collect::<PyResult<Vec<_>>>()?;
            PyList::new(py, converted)?.into_any()
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (key, item) in map {
                dict.set_item(key, to_py(py, item)?)?;
            }
            dict.into_any()
        }
    })
}

/// The fast path: exact built-in types read through CPython's C API, without a Python object
/// wrapper per entry. Anything else (a subclass, a date, a custom object) goes to the generic
/// conversion, which can run Python code; a strong reference is held across that, so the value
/// cannot be freed even if the code mutates the container being walked.
///
/// Not built for free-threaded Python, where walking a dict this way is not synchronised.
#[cfg(not(Py_GIL_DISABLED))]
mod fast {
    use std::ptr::addr_of_mut;

    use pyo3::ffi;
    use pyo3::prelude::*;
    use serde_json::{Map, Number, Value};

    use super::{convert_generic, key_text, NonFinite, CYCLE, MAX_DEPTH, TOO_DEEP};

    /// The generic conversion of a value the fast path does not know.
    fn slow(
        py: Python<'_>,
        obj: *mut ffi::PyObject,
        non_finite: NonFinite,
        path: &mut Vec<usize>,
    ) -> PyResult<Value> {
        // SAFETY: `obj` is a live object the caller holds a borrowed reference to; this takes its
        // own strong one.
        let bound = unsafe { Bound::from_borrowed_ptr(py, obj) };
        convert_generic(&bound, non_finite, path)
    }

    /// The text of an exactly-`str` object, or `None` for one that cannot be encoded (a lone
    /// surrogate), which the generic path replaces.
    ///
    /// # Safety
    /// `obj` must be a live `str` and the GIL must be held; the result is valid until `obj` dies.
    unsafe fn text<'a>(obj: *mut ffi::PyObject) -> Option<&'a str> {
        let mut size: ffi::Py_ssize_t = 0;
        let data = ffi::PyUnicode_AsUTF8AndSize(obj, &mut size);
        if data.is_null() {
            ffi::PyErr_Clear();
            return None;
        }
        // CPython guarantees the buffer is valid UTF-8 of `size` bytes.
        Some(std::str::from_utf8_unchecked(std::slice::from_raw_parts(
            data.cast::<u8>(),
            size as usize,
        )))
    }

    pub fn convert(
        py: Python<'_>,
        obj: *mut ffi::PyObject,
        non_finite: NonFinite,
        path: &mut Vec<usize>,
    ) -> PyResult<Value> {
        // SAFETY: the GIL is held (`py`), `obj` is a live object, and every pointer taken from a
        // container below is used before any Python code that could free it runs (the `slow` path
        // takes its own strong reference first).
        unsafe {
            if obj == ffi::Py_None() {
                return Ok(Value::Null);
            }
            let ty = ffi::Py_TYPE(obj);
            if ty == addr_of_mut!(ffi::PyUnicode_Type) {
                if let Some(text) = text(obj) {
                    return Ok(Value::String(text.to_owned()));
                }
            } else if ty == addr_of_mut!(ffi::PyLong_Type) {
                let mut overflow = 0;
                let value = ffi::PyLong_AsLongLongAndOverflow(obj, &mut overflow);
                if overflow == 0 && !(value == -1 && !ffi::PyErr_Occurred().is_null()) {
                    return Ok(Value::from(value));
                }
                ffi::PyErr_Clear();
            } else if ty == addr_of_mut!(ffi::PyBool_Type) {
                return Ok(Value::Bool(obj == ffi::Py_True()));
            } else if ty == addr_of_mut!(ffi::PyFloat_Type) {
                let value = ffi::PyFloat_AsDouble(obj);
                if let Some(number) = Number::from_f64(value) {
                    return Ok(Value::Number(number));
                }
                // NaN and infinity: the generic path applies the caller's rule for them
            } else if ty == addr_of_mut!(ffi::PyDict_Type) {
                return dict(py, obj, non_finite, path);
            } else if ty == addr_of_mut!(ffi::PyList_Type) || ty == addr_of_mut!(ffi::PyTuple_Type)
            {
                return sequence(
                    py,
                    obj,
                    ty == addr_of_mut!(ffi::PyList_Type),
                    non_finite,
                    path,
                );
            }
        }
        slow(py, obj, non_finite, path)
    }

    /// Enters a container: `Err` text is the marker to put in its place (a cycle or too deep).
    fn enter(obj: *mut ffi::PyObject, path: &mut Vec<usize>) -> Result<(), &'static str> {
        let id = obj as usize;
        if path.contains(&id) {
            return Err(CYCLE);
        }
        if path.len() >= MAX_DEPTH {
            return Err(TOO_DEEP);
        }
        path.push(id);
        Ok(())
    }

    unsafe fn dict(
        py: Python<'_>,
        obj: *mut ffi::PyObject,
        non_finite: NonFinite,
        path: &mut Vec<usize>,
    ) -> PyResult<Value> {
        if let Err(marker) = enter(obj, path) {
            return Ok(Value::String(marker.to_string()));
        }
        let mut map = Map::new();
        let mut position: ffi::Py_ssize_t = 0;
        let (mut key, mut value): (*mut ffi::PyObject, *mut ffi::PyObject) =
            (std::ptr::null_mut(), std::ptr::null_mut());
        let mut result = Ok(());
        while ffi::PyDict_Next(obj, &mut position, &mut key, &mut value) != 0 {
            let name = if ffi::Py_TYPE(key) == addr_of_mut!(ffi::PyUnicode_Type) {
                text(key).map(str::to_owned)
            } else {
                None
            };
            let name = match name {
                Some(name) => Ok(name),
                None => key_text(&Bound::from_borrowed_ptr(py, key)),
            };
            let entry = name.and_then(|name| {
                convert(py, value, non_finite, path).map(|converted| (name, converted))
            });
            match entry {
                Ok((name, converted)) => {
                    map.insert(name, converted);
                }
                Err(error) => {
                    result = Err(error);
                    break;
                }
            }
        }
        path.pop();
        result.map(|()| Value::Object(map))
    }

    unsafe fn sequence(
        py: Python<'_>,
        obj: *mut ffi::PyObject,
        is_list: bool,
        non_finite: NonFinite,
        path: &mut Vec<usize>,
    ) -> PyResult<Value> {
        if let Err(marker) = enter(obj, path) {
            return Ok(Value::String(marker.to_string()));
        }
        let size = if is_list {
            ffi::PyList_Size(obj)
        } else {
            ffi::PyTuple_Size(obj)
        };
        let mut items = Vec::with_capacity(size.max(0) as usize);
        let mut result = Ok(());
        for index in 0..size {
            // a list can shrink while a Python callback runs; stop rather than read past the end
            let item = if is_list {
                ffi::PyList_GetItem(obj, index)
            } else {
                ffi::PyTuple_GetItem(obj, index)
            };
            if item.is_null() {
                ffi::PyErr_Clear();
                break;
            }
            match convert(py, item, non_finite, path) {
                Ok(converted) => items.push(converted),
                Err(error) => {
                    result = Err(error);
                    break;
                }
            }
        }
        path.pop();
        result.map(|()| Value::Array(items))
    }
}
