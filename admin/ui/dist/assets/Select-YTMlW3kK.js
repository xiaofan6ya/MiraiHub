import{d as A,f as P,y as Xe,x as ge,i as ut,A as u,K as ao,l as Le,s as so,u as co,B as oe,n as ft,D as uo,w as Oe,p as Tt,I as Ft,F as fo,H as ho,z as vo,L as go}from"./_plugin-vue_export-helper-GEDmboVY.js";import{x as Ie,a1 as gt,R as Ot,Q as It,I as ct,bd as bo,T as Ge,an as Ne,bq as lt,Z as ze,br as ht,t as po,n as W,p as D,o as J,q as de,r as Fe,aO as Pt,a0 as Ye,am as mo,S as Co,ad as wo,y as Qe,ac as vt,A as xe,a4 as Ze,bs as yo,G as ee,a6 as Me,bt as xo,j as So,bu as Ro,ai as q,w as ko,F as ve,bv as bt,m as To,bw as Fo,bx as Oo,L as Io,by as Po,aJ as zo,aK as Mo,aL as Bo,aP as dt,aM as pt,z as mt,v as _o,D as $o,E as Eo,aN as Ao,aR as No,a5 as Wo,bz as Lo}from"./index-h_AcSpnM.js";import{N as Do}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-7_5VFdPg.js";import{h as We,a as Ho,c as jo}from"./create-DLefnRiD.js";function Ct(e){return e&-e}class zt{constructor(o,n){this.l=o,this.min=n;const r=new Array(o+1);for(let l=0;l<o+1;++l)r[l]=0;this.ft=r}add(o,n){if(n===0)return;const{l:r,ft:l}=this;for(o+=1;o<=r;)l[o]+=n,o+=Ct(o)}get(o){return this.sum(o+1)-this.sum(o)}sum(o){if(o===void 0&&(o=this.l),o<=0)return 0;const{ft:n,min:r,l}=this;if(o>l)throw new Error("[FinweckTree.sum]: `i` is larger than length.");let f=o*r;for(;o>0;)f+=n[o],o-=Ct(o);return f}getBound(o){let n=0,r=this.l;for(;r>n;){const l=Math.floor((n+r)/2),f=this.sum(l);if(f>o){r=l;continue}else if(f<o){if(n===l)return this.sum(n+1)<=o?n+1:l;n=l}else return l}return n}}let qe;function Vo(){return typeof document>"u"?!1:(qe===void 0&&("matchMedia"in window?qe=window.matchMedia("(pointer:coarse)").matches:qe=!1),qe)}let rt;function wt(){return typeof document>"u"?1:(rt===void 0&&(rt="chrome"in window?window.devicePixelRatio:1),rt)}const Mt="VVirtualListXScroll";function Ko({columnsRef:e,renderColRef:o,renderItemWithColsRef:n}){const r=P(0),l=P(0),f=A(()=>{const C=e.value;if(C.length===0)return null;const w=new zt(C.length,0);return C.forEach((S,T)=>{w.add(T,S.width)}),w}),d=Ie(()=>{const C=f.value;return C!==null?Math.max(C.getBound(l.value)-1,0):0}),i=C=>{const w=f.value;return w!==null?w.sum(C):0},g=Ie(()=>{const C=f.value;return C!==null?Math.min(C.getBound(l.value+r.value)+1,e.value.length-1):0});return Xe(Mt,{startIndexRef:d,endIndexRef:g,columnsRef:e,renderColRef:o,renderItemWithColsRef:n,getLeft:i}),{listWidthRef:r,scrollLeftRef:l}}const yt=ge({name:"VirtualListRow",props:{index:{type:Number,required:!0},item:{type:Object,required:!0}},setup(){const{startIndexRef:e,endIndexRef:o,columnsRef:n,getLeft:r,renderColRef:l,renderItemWithColsRef:f}=ut(Mt);return{startIndex:e,endIndex:o,columns:n,renderCol:l,renderItemWithCols:f,getLeft:r}},render(){const{startIndex:e,endIndex:o,columns:n,renderCol:r,renderItemWithCols:l,getLeft:f,item:d}=this;if(l!=null)return l({itemIndex:this.index,startColIndex:e,endColIndex:o,allColumns:n,item:d,getLeft:f});if(r!=null){const i=[];for(let g=e;g<=o;++g){const C=n[g];i.push(r({column:C,left:f(g),item:d}))}return i}return null}}),Uo=Ge(".v-vl",{maxHeight:"inherit",height:"100%",overflow:"auto",minWidth:"1px"},[Ge("&:not(.v-vl--show-scrollbar)",{scrollbarWidth:"none"},[Ge("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",{width:0,height:0,display:"none"})])]),qo=ge({name:"VirtualList",inheritAttrs:!1,props:{showScrollbar:{type:Boolean,default:!0},columns:{type:Array,default:()=>[]},renderCol:Function,renderItemWithCols:Function,items:{type:Array,default:()=>[]},itemSize:{type:Number,required:!0},itemResizable:Boolean,itemsStyle:[String,Object],visibleItemsTag:{type:[String,Object],default:"div"},visibleItemsProps:Object,ignoreItemResize:Boolean,onScroll:Function,onWheel:Function,onResize:Function,defaultScrollKey:[Number,String],defaultScrollIndex:Number,keyField:{type:String,default:"key"},paddingTop:{type:[Number,String],default:0},paddingBottom:{type:[Number,String],default:0}},setup(e){const o=It();Uo.mount({id:"vueuc/virtual-list",head:!0,anchorMetaName:Ot,ssr:o}),Le(()=>{const{defaultScrollIndex:c,defaultScrollKey:x}=e;c!=null?F({index:c}):x!=null&&F({key:x})});let n=!1,r=!1;so(()=>{if(n=!1,!r){r=!0;return}F({top:O.value,left:d.value})}),co(()=>{n=!0,r||(r=!0)});const l=Ie(()=>{if(e.renderCol==null&&e.renderItemWithCols==null||e.columns.length===0)return;let c=0;return e.columns.forEach(x=>{c+=x.width}),c}),f=A(()=>{const c=new Map,{keyField:x}=e;return e.items.forEach((N,z)=>{c.set(N[x],z)}),c}),{scrollLeftRef:d,listWidthRef:i}=Ko({columnsRef:oe(e,"columns"),renderColRef:oe(e,"renderCol"),renderItemWithColsRef:oe(e,"renderItemWithCols")}),g=P(null),C=P(void 0),w=new Map,S=A(()=>{const{items:c,itemSize:x,keyField:N}=e,z=new zt(c.length,x);return c.forEach((X,H)=>{const K=X[N],V=w.get(K);V!==void 0&&z.add(H,V)}),z}),T=P(0),O=P(0),m=Ie(()=>Math.max(S.value.getBound(O.value-ct(e.paddingTop))-1,0)),b=A(()=>{const{value:c}=C;if(c===void 0)return[];const{items:x,itemSize:N}=e,z=m.value,X=Math.min(z+Math.ceil(c/N+1),x.length-1),H=[];for(let K=z;K<=X;++K)H.push(x[K]);return H}),F=(c,x)=>{if(typeof c=="number"){_(c,x,"auto");return}const{left:N,top:z,index:X,key:H,position:K,behavior:V,debounce:U=!0}=c;if(N!==void 0||z!==void 0)_(N,z,V);else if(X!==void 0)I(X,V,U);else if(H!==void 0){const ae=f.value.get(H);ae!==void 0&&I(ae,V,U)}else K==="bottom"?_(0,Number.MAX_SAFE_INTEGER,V):K==="top"&&_(0,0,V)};let R,y=null;function I(c,x,N){const z=g.value;if(z==null)return;const{value:X}=S,H=X.sum(c)+ct(e.paddingTop);if(!N)z.scrollTo({left:0,top:H,behavior:x});else{R=c,y!==null&&window.clearTimeout(y),y=window.setTimeout(()=>{R=void 0,y=null},16);const{scrollTop:K,offsetHeight:V}=z;if(H>K){const U=X.get(c);H+U<=K+V||z.scrollTo({left:0,top:H+U-V,behavior:x})}else z.scrollTo({left:0,top:H,behavior:x})}}function _(c,x,N){const z=g.value;z?.scrollTo({left:c,top:x,behavior:N})}function L(c,x){var N,z,X;if(n||e.ignoreItemResize||ie(x.target))return;const{value:H}=S,K=f.value.get(c),V=H.get(K),U=(X=(z=(N=x.borderBoxSize)===null||N===void 0?void 0:N[0])===null||z===void 0?void 0:z.blockSize)!==null&&X!==void 0?X:x.contentRect.height;if(U===V)return;U-e.itemSize===0?w.delete(c):w.set(c,U-e.itemSize);const se=U-V;if(se===0)return;H.add(K,se);const a=g.value;if(a!=null){if(R===void 0){const v=H.sum(K);a.scrollTop>v&&a.scrollBy(0,se)}else if(K<R)a.scrollBy(0,se);else if(K===R){const v=H.sum(K);U+v>a.scrollTop+a.offsetHeight&&a.scrollBy(0,se)}ne()}T.value++}const Y=!Vo();let j=!1;function le(c){var x;(x=e.onScroll)===null||x===void 0||x.call(e,c),(!Y||!j)&&ne()}function re(c){var x;if((x=e.onWheel)===null||x===void 0||x.call(e,c),Y){const N=g.value;if(N!=null){if(c.deltaX===0&&(N.scrollTop===0&&c.deltaY<=0||N.scrollTop+N.offsetHeight>=N.scrollHeight&&c.deltaY>=0))return;c.preventDefault(),N.scrollTop+=c.deltaY/wt(),N.scrollLeft+=c.deltaX/wt(),ne(),j=!0,bo(()=>{j=!1})}}}function Q(c){if(n||ie(c.target))return;if(e.renderCol==null&&e.renderItemWithCols==null){if(c.contentRect.height===C.value)return}else if(c.contentRect.height===C.value&&c.contentRect.width===i.value)return;C.value=c.contentRect.height,i.value=c.contentRect.width;const{onResize:x}=e;x!==void 0&&x(c)}function ne(){const{value:c}=g;c!=null&&(O.value=c.scrollTop,d.value=c.scrollLeft)}function ie(c){let x=c;for(;x!==null;){if(x.style.display==="none")return!0;x=x.parentElement}return!1}return{listHeight:C,listStyle:{overflow:"auto"},keyToIndex:f,itemsStyle:A(()=>{const{itemResizable:c}=e,x=Ne(S.value.sum());return T.value,[e.itemsStyle,{boxSizing:"content-box",width:Ne(l.value),height:c?"":x,minHeight:c?x:"",paddingTop:Ne(e.paddingTop),paddingBottom:Ne(e.paddingBottom)}]}),visibleItemsStyle:A(()=>(T.value,{transform:`translateY(${Ne(S.value.sum(m.value))})`})),viewportItems:b,listElRef:g,itemsElRef:P(null),scrollTo:F,handleListResize:Q,handleListScroll:le,handleListWheel:re,handleItemResize:L}},render(){const{itemResizable:e,keyField:o,keyToIndex:n,visibleItemsTag:r}=this;return u(gt,{onResize:this.handleListResize},{default:()=>{var l,f;return u("div",ao(this.$attrs,{class:["v-vl",this.showScrollbar&&"v-vl--show-scrollbar"],onScroll:this.handleListScroll,onWheel:this.handleListWheel,ref:"listElRef"}),[this.items.length!==0?u("div",{ref:"itemsElRef",class:"v-vl-items",style:this.itemsStyle},[u(r,Object.assign({class:"v-vl-visible-items",style:this.visibleItemsStyle},this.visibleItemsProps),{default:()=>{const{renderCol:d,renderItemWithCols:i}=this;return this.viewportItems.map(g=>{const C=g[o],w=n.get(C),S=d!=null?u(yt,{index:w,item:g}):void 0,T=i!=null?u(yt,{index:w,item:g}):void 0,O=this.$slots.default({item:g,renderedCols:S,renderedItemWithCols:T,index:w})[0];return e?u(gt,{key:C,onResize:m=>this.handleItemResize(C,m)},{default:()=>O}):(O.key=C,O)})}})]):(f=(l=this.$slots).empty)===null||f===void 0?void 0:f.call(l)])}})}}),Ce="v-hidden",Go=Ge("[v-hidden]",{display:"none!important"}),xt=ge({name:"Overflow",props:{getCounter:Function,getTail:Function,updateCounter:Function,onUpdateCount:Function,onUpdateOverflow:Function},setup(e,{slots:o}){const n=P(null),r=P(null);function l(d){const{value:i}=n,{getCounter:g,getTail:C}=e;let w;if(g!==void 0?w=g():w=r.value,!i||!w)return;w.hasAttribute(Ce)&&w.removeAttribute(Ce);const{children:S}=i;if(d.showAllItemsBeforeCalculate)for(const I of S)I.hasAttribute(Ce)&&I.removeAttribute(Ce);const T=i.offsetWidth,O=[],m=o.tail?C?.():null;let b=m?m.offsetWidth:0,F=!1;const R=i.children.length-(o.tail?1:0);for(let I=0;I<R-1;++I){if(I<0)continue;const _=S[I];if(F){_.hasAttribute(Ce)||_.setAttribute(Ce,"");continue}else _.hasAttribute(Ce)&&_.removeAttribute(Ce);const L=_.offsetWidth;if(b+=L,O[I]=L,b>T){const{updateCounter:Y}=e;for(let j=I;j>=0;--j){const le=R-1-j;Y!==void 0?Y(le):w.textContent=`${le}`;const re=w.offsetWidth;if(b-=O[j],b+re<=T||j===0){F=!0,I=j-1,m&&(I===-1?(m.style.maxWidth=`${T-re}px`,m.style.boxSizing="border-box"):m.style.maxWidth="");const{onUpdateCount:Q}=e;Q&&Q(le);break}}}}const{onUpdateOverflow:y}=e;F?y!==void 0&&y(!0):(y!==void 0&&y(!1),w.setAttribute(Ce,""))}const f=It();return Go.mount({id:"vueuc/overflow",head:!0,anchorMetaName:Ot,ssr:f}),Le(()=>l({showAllItemsBeforeCalculate:!1})),{selfRef:n,counterRef:r,sync:l}},render(){const{$slots:e}=this;return ft(()=>this.sync({showAllItemsBeforeCalculate:!1})),u("div",{class:"v-overflow",ref:"selfRef"},[uo(e,"default"),e.counter?e.counter():u("span",{style:{display:"inline-block"},ref:"counterRef"}),e.tail?e.tail():null])}});function Bt(e,o){o&&(Le(()=>{const{value:n}=e;n&&lt.registerHandler(n,o)}),Oe(e,(n,r)=>{r&&lt.unregisterHandler(r)},{deep:!1}),Tt(()=>{const{value:n}=e;n&&lt.unregisterHandler(n)}))}function St(e){switch(typeof e){case"string":return e||void 0;case"number":return String(e);default:return}}function it(e){const o=e.filter(n=>n!==void 0);if(o.length!==0)return o.length===1?o[0]:n=>{e.forEach(r=>{r&&r(n)})}}const Xo=ge({name:"Checkmark",render(){return u("svg",{xmlns:"http://www.w3.org/2000/svg",viewBox:"0 0 16 16"},u("g",{fill:"none"},u("path",{d:"M14.046 3.486a.75.75 0 0 1-.032 1.06l-7.93 7.474a.85.85 0 0 1-1.188-.022l-2.68-2.72a.75.75 0 1 1 1.068-1.053l2.234 2.267l7.468-7.038a.75.75 0 0 1 1.06.032z",fill:"currentColor"})))}}),Yo=ge({props:{onFocus:Function,onBlur:Function},setup(e){return()=>u("div",{style:"width: 0; height: 0",tabindex:0,onFocus:e.onFocus,onBlur:e.onBlur})}}),Rt=ge({name:"NBaseSelectGroupHeader",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(){const{renderLabelRef:e,renderOptionRef:o,labelFieldRef:n,nodePropsRef:r}=ut(ht);return{labelField:n,nodeProps:r,renderLabel:e,renderOption:o}},render(){const{clsPrefix:e,renderLabel:o,renderOption:n,nodeProps:r,tmNode:{rawNode:l}}=this,f=r?.(l),d=o?o(l,!1):ze(l[this.labelField],l,!1),i=u("div",Object.assign({},f,{class:[`${e}-base-select-group-header`,f?.class]}),d);return l.render?l.render({node:i,option:l}):n?n({node:i,option:l,selected:!1}):i}});function Jo(e,o){return u(Ft,{name:"fade-in-scale-up-transition"},{default:()=>e?u(po,{clsPrefix:o,class:`${o}-base-select-option__check`},{default:()=>u(Xo)}):null})}const kt=ge({name:"NBaseSelectOption",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(e){const{valueRef:o,pendingTmNodeRef:n,multipleRef:r,valueSetRef:l,renderLabelRef:f,renderOptionRef:d,labelFieldRef:i,valueFieldRef:g,showCheckmarkRef:C,nodePropsRef:w,handleOptionClick:S,handleOptionMouseEnter:T}=ut(ht),O=Ie(()=>{const{value:R}=n;return R?e.tmNode.key===R.key:!1});function m(R){const{tmNode:y}=e;y.disabled||S(R,y)}function b(R){const{tmNode:y}=e;y.disabled||T(R,y)}function F(R){const{tmNode:y}=e,{value:I}=O;y.disabled||I||T(R,y)}return{multiple:r,isGrouped:Ie(()=>{const{tmNode:R}=e,{parent:y}=R;return y&&y.rawNode.type==="group"}),showCheckmark:C,nodeProps:w,isPending:O,isSelected:Ie(()=>{const{value:R}=o,{value:y}=r;if(R===null)return!1;const I=e.tmNode.rawNode[g.value];if(y){const{value:_}=l;return _.has(I)}else return R===I}),labelField:i,renderLabel:f,renderOption:d,handleMouseMove:F,handleMouseEnter:b,handleClick:m}},render(){const{clsPrefix:e,tmNode:{rawNode:o},isSelected:n,isPending:r,isGrouped:l,showCheckmark:f,nodeProps:d,renderOption:i,renderLabel:g,handleClick:C,handleMouseEnter:w,handleMouseMove:S}=this,T=Jo(n,e),O=g?[g(o,n),f&&T]:[ze(o[this.labelField],o,n),f&&T],m=d?.(o),b=u("div",Object.assign({},m,{class:[`${e}-base-select-option`,o.class,m?.class,{[`${e}-base-select-option--disabled`]:o.disabled,[`${e}-base-select-option--selected`]:n,[`${e}-base-select-option--grouped`]:l,[`${e}-base-select-option--pending`]:r,[`${e}-base-select-option--show-checkmark`]:f}],style:[m?.style||"",o.style||""],onClick:it([C,m?.onClick]),onMouseenter:it([w,m?.onMouseenter]),onMousemove:it([S,m?.onMousemove])}),u("div",{class:`${e}-base-select-option__content`},O));return o.render?o.render({node:b,option:o,selected:n}):i?i({node:b,option:o,selected:n}):b}}),Qo=W("base-select-menu",`
 line-height: 1.5;
 outline: none;
 z-index: 0;
 position: relative;
 border-radius: var(--n-border-radius);
 transition:
 background-color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier);
 background-color: var(--n-color);
`,[W("scrollbar",`
 max-height: var(--n-height);
 `),W("virtual-list",`
 max-height: var(--n-height);
 `),W("base-select-option",`
 min-height: var(--n-option-height);
 font-size: var(--n-option-font-size);
 display: flex;
 align-items: center;
 `,[D("content",`
 z-index: 1;
 white-space: nowrap;
 text-overflow: ellipsis;
 overflow: hidden;
 `)]),W("base-select-group-header",`
 min-height: var(--n-option-height);
 font-size: .93em;
 display: flex;
 align-items: center;
 `),W("base-select-menu-option-wrapper",`
 position: relative;
 width: 100%;
 `),D("loading, empty",`
 display: flex;
 padding: 12px 32px;
 flex: 1;
 justify-content: center;
 `),D("loading",`
 color: var(--n-loading-color);
 font-size: var(--n-loading-size);
 `),D("header",`
 padding: 8px var(--n-option-padding-left);
 font-size: var(--n-option-font-size);
 transition: 
 color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 border-bottom: 1px solid var(--n-action-divider-color);
 color: var(--n-action-text-color);
 `),D("action",`
 padding: 8px var(--n-option-padding-left);
 font-size: var(--n-option-font-size);
 transition: 
 color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 border-top: 1px solid var(--n-action-divider-color);
 color: var(--n-action-text-color);
 `),W("base-select-group-header",`
 position: relative;
 cursor: default;
 padding: var(--n-option-padding);
 color: var(--n-group-header-text-color);
 `),W("base-select-option",`
 cursor: pointer;
 position: relative;
 padding: var(--n-option-padding);
 transition:
 color .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 box-sizing: border-box;
 color: var(--n-option-text-color);
 opacity: 1;
 `,[J("show-checkmark",`
 padding-right: calc(var(--n-option-padding-right) + 20px);
 `),de("&::before",`
 content: "";
 position: absolute;
 left: 4px;
 right: 4px;
 top: 0;
 bottom: 0;
 border-radius: var(--n-border-radius);
 transition: background-color .3s var(--n-bezier);
 `),de("&:active",`
 color: var(--n-option-text-color-pressed);
 `),J("grouped",`
 padding-left: calc(var(--n-option-padding-left) * 1.5);
 `),J("pending",[de("&::before",`
 background-color: var(--n-option-color-pending);
 `)]),J("selected",`
 color: var(--n-option-text-color-active);
 `,[de("&::before",`
 background-color: var(--n-option-color-active);
 `),J("pending",[de("&::before",`
 background-color: var(--n-option-color-active-pending);
 `)])]),J("disabled",`
 cursor: not-allowed;
 `,[Fe("selected",`
 color: var(--n-option-text-color-disabled);
 `),J("selected",`
 opacity: var(--n-option-opacity-disabled);
 `)]),D("check",`
 font-size: 16px;
 position: absolute;
 right: calc(var(--n-option-padding-right) - 4px);
 top: calc(50% - 7px);
 color: var(--n-option-check-color);
 transition: color .3s var(--n-bezier);
 `,[Pt({enterScale:"0.5"})])])]),Zo=ge({name:"InternalSelectMenu",props:Object.assign(Object.assign({},xe.props),{clsPrefix:{type:String,required:!0},scrollable:{type:Boolean,default:!0},treeMate:{type:Object,required:!0},multiple:Boolean,size:{type:String,default:"medium"},value:{type:[String,Number,Array],default:null},autoPending:Boolean,virtualScroll:{type:Boolean,default:!0},show:{type:Boolean,default:!0},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},loading:Boolean,focusable:Boolean,renderLabel:Function,renderOption:Function,nodeProps:Function,showCheckmark:{type:Boolean,default:!0},onMousedown:Function,onScroll:Function,onFocus:Function,onBlur:Function,onKeyup:Function,onKeydown:Function,onTabOut:Function,onMouseenter:Function,onMouseleave:Function,onResize:Function,resetMenuOnOptionsChange:{type:Boolean,default:!0},inlineThemeDisabled:Boolean,scrollbarProps:Object,onToggle:Function}),setup(e){const{mergedClsPrefixRef:o,mergedRtlRef:n,mergedComponentPropsRef:r}=Qe(e),l=vt("InternalSelectMenu",n,o),f=xe("InternalSelectMenu","-internal-select-menu",Qo,yo,e,oe(e,"clsPrefix")),d=P(null),i=P(null),g=P(null),C=A(()=>e.treeMate.getFlattenedNodes()),w=A(()=>Ho(C.value)),S=P(null);function T(){const{treeMate:a}=e;let v=null;const{value:G}=e;G===null?v=a.getFirstAvailableNode():(e.multiple?v=a.getNode((G||[])[(G||[]).length-1]):v=a.getNode(G),(!v||v.disabled)&&(v=a.getFirstAvailableNode())),z(v||null)}function O(){const{value:a}=S;a&&!e.treeMate.getNode(a.key)&&(S.value=null)}let m;Oe(()=>e.show,a=>{a?m=Oe(()=>e.treeMate,()=>{e.resetMenuOnOptionsChange?(e.autoPending?T():O(),ft(X)):O()},{immediate:!0}):m?.()},{immediate:!0}),Tt(()=>{m?.()});const b=A(()=>ct(f.value.self[ee("optionHeight",e.size)])),F=A(()=>Me(f.value.self[ee("padding",e.size)])),R=A(()=>e.multiple&&Array.isArray(e.value)?new Set(e.value):new Set),y=A(()=>{const a=C.value;return a&&a.length===0}),I=A(()=>{var a,v;return(v=(a=r?.value)===null||a===void 0?void 0:a.Select)===null||v===void 0?void 0:v.renderEmpty});function _(a){const{onToggle:v}=e;v&&v(a)}function L(a){const{onScroll:v}=e;v&&v(a)}function Y(a){var v;(v=g.value)===null||v===void 0||v.sync(),L(a)}function j(){var a;(a=g.value)===null||a===void 0||a.sync()}function le(){const{value:a}=S;return a||null}function re(a,v){v.disabled||z(v,!1)}function Q(a,v){v.disabled||_(v)}function ne(a){var v;We(a,"action")||(v=e.onKeyup)===null||v===void 0||v.call(e,a)}function ie(a){var v;We(a,"action")||(v=e.onKeydown)===null||v===void 0||v.call(e,a)}function c(a){var v;(v=e.onMousedown)===null||v===void 0||v.call(e,a),!e.focusable&&a.preventDefault()}function x(){const{value:a}=S;a&&z(a.getNext({loop:!0}),!0)}function N(){const{value:a}=S;a&&z(a.getPrev({loop:!0}),!0)}function z(a,v=!1){S.value=a,v&&X()}function X(){var a,v;const G=S.value;if(!G)return;const fe=w.value(G.key);fe!==null&&(e.virtualScroll?(a=i.value)===null||a===void 0||a.scrollTo({index:fe}):(v=g.value)===null||v===void 0||v.scrollTo({index:fe,elSize:b.value}))}function H(a){var v,G;!((v=d.value)===null||v===void 0)&&v.contains(a.target)&&((G=e.onFocus)===null||G===void 0||G.call(e,a))}function K(a){var v,G;!((v=d.value)===null||v===void 0)&&v.contains(a.relatedTarget)||(G=e.onBlur)===null||G===void 0||G.call(e,a)}Xe(ht,{handleOptionMouseEnter:re,handleOptionClick:Q,valueSetRef:R,pendingTmNodeRef:S,nodePropsRef:oe(e,"nodeProps"),showCheckmarkRef:oe(e,"showCheckmark"),multipleRef:oe(e,"multiple"),valueRef:oe(e,"value"),renderLabelRef:oe(e,"renderLabel"),renderOptionRef:oe(e,"renderOption"),labelFieldRef:oe(e,"labelField"),valueFieldRef:oe(e,"valueField")}),Xe(xo,d),Le(()=>{const{value:a}=g;a&&a.sync()});const V=A(()=>{const{size:a}=e,{common:{cubicBezierEaseInOut:v},self:{height:G,borderRadius:fe,color:he,groupHeaderTextColor:ce,actionDividerColor:we,optionTextColorPressed:be,optionTextColor:Se,optionTextColorDisabled:pe,optionTextColorActive:Be,optionOpacityDisabled:_e,optionCheckColor:Re,actionTextColor:ke,optionColorPending:$e,optionColorActive:Ee,loadingColor:Ae,loadingSize:Pe,optionColorActivePending:Te,[ee("optionFontSize",a)]:ue,[ee("optionHeight",a)]:s,[ee("optionPadding",a)]:p}}=f.value;return{"--n-height":G,"--n-action-divider-color":we,"--n-action-text-color":ke,"--n-bezier":v,"--n-border-radius":fe,"--n-color":he,"--n-option-font-size":ue,"--n-group-header-text-color":ce,"--n-option-check-color":Re,"--n-option-color-pending":$e,"--n-option-color-active":Ee,"--n-option-color-active-pending":Te,"--n-option-height":s,"--n-option-opacity-disabled":_e,"--n-option-text-color":Se,"--n-option-text-color-active":Be,"--n-option-text-color-disabled":pe,"--n-option-text-color-pressed":be,"--n-option-padding":p,"--n-option-padding-left":Me(p,"left"),"--n-option-padding-right":Me(p,"right"),"--n-loading-color":Ae,"--n-loading-size":Pe}}),{inlineThemeDisabled:U}=e,ae=U?Ze("internal-select-menu",A(()=>e.size[0]),V,e):void 0,se={selfRef:d,next:x,prev:N,getPendingTmNode:le};return Bt(d,e.onResize),Object.assign({mergedTheme:f,mergedClsPrefix:o,rtlEnabled:l,virtualListRef:i,scrollbarRef:g,itemSize:b,padding:F,flattenedNodes:C,empty:y,mergedRenderEmpty:I,virtualListContainer(){const{value:a}=i;return a?.listElRef},virtualListContent(){const{value:a}=i;return a?.itemsElRef},doScroll:L,handleFocusin:H,handleFocusout:K,handleKeyUp:ne,handleKeyDown:ie,handleMouseDown:c,handleVirtualListResize:j,handleVirtualListScroll:Y,cssVars:U?void 0:V,themeClass:ae?.themeClass,onRender:ae?.onRender},se)},render(){const{$slots:e,virtualScroll:o,clsPrefix:n,mergedTheme:r,themeClass:l,onRender:f}=this;return f?.(),u("div",{ref:"selfRef",tabindex:this.focusable?0:-1,class:[`${n}-base-select-menu`,this.rtlEnabled&&`${n}-base-select-menu--rtl`,l,this.multiple&&`${n}-base-select-menu--multiple`],style:this.cssVars,onFocusin:this.handleFocusin,onFocusout:this.handleFocusout,onKeyup:this.handleKeyUp,onKeydown:this.handleKeyDown,onMousedown:this.handleMouseDown,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},Ye(e.header,d=>d&&u("div",{class:`${n}-base-select-menu__header`,"data-header":!0,key:"header"},d)),this.loading?u("div",{class:`${n}-base-select-menu__loading`},u(mo,{clsPrefix:n,strokeWidth:20})):this.empty?u("div",{class:`${n}-base-select-menu__empty`,"data-empty":!0},wo(e.empty,()=>{var d;return[((d=this.mergedRenderEmpty)===null||d===void 0?void 0:d.call(this))||u(Do,{theme:r.peers.Empty,themeOverrides:r.peerOverrides.Empty,size:this.size})]})):u(Co,Object.assign({ref:"scrollbarRef",theme:r.peers.Scrollbar,themeOverrides:r.peerOverrides.Scrollbar,scrollable:this.scrollable,container:o?this.virtualListContainer:void 0,content:o?this.virtualListContent:void 0,onScroll:o?void 0:this.doScroll},this.scrollbarProps),{default:()=>o?u(qo,{ref:"virtualListRef",class:`${n}-virtual-list`,items:this.flattenedNodes,itemSize:this.itemSize,showScrollbar:!1,paddingTop:this.padding.top,paddingBottom:this.padding.bottom,onResize:this.handleVirtualListResize,onScroll:this.handleVirtualListScroll,itemResizable:!0},{default:({item:d})=>d.isGroup?u(Rt,{key:d.key,clsPrefix:n,tmNode:d}):d.ignored?null:u(kt,{clsPrefix:n,key:d.key,tmNode:d})}):u("div",{class:`${n}-base-select-menu-option-wrapper`,style:{paddingTop:this.padding.top,paddingBottom:this.padding.bottom}},this.flattenedNodes.map(d=>d.isGroup?u(Rt,{key:d.key,clsPrefix:n,tmNode:d}):u(kt,{clsPrefix:n,key:d.key,tmNode:d})))}),Ye(e.action,d=>d&&[u("div",{class:`${n}-base-select-menu__action`,"data-action":!0,key:"action"},d),u(Yo,{onFocus:this.onTabOut,key:"focus-detector"})]))}});function en(e){const{textColor2:o,primaryColorHover:n,primaryColorPressed:r,primaryColor:l,infoColor:f,successColor:d,warningColor:i,errorColor:g,baseColor:C,borderColor:w,opacityDisabled:S,tagColor:T,closeIconColor:O,closeIconColorHover:m,closeIconColorPressed:b,borderRadiusSmall:F,fontSizeMini:R,fontSizeTiny:y,fontSizeSmall:I,fontSizeMedium:_,heightMini:L,heightTiny:Y,heightSmall:j,heightMedium:le,closeColorHover:re,closeColorPressed:Q,buttonColor2Hover:ne,buttonColor2Pressed:ie,fontWeightStrong:c}=e;return Object.assign(Object.assign({},Ro),{closeBorderRadius:F,heightTiny:L,heightSmall:Y,heightMedium:j,heightLarge:le,borderRadius:F,opacityDisabled:S,fontSizeTiny:R,fontSizeSmall:y,fontSizeMedium:I,fontSizeLarge:_,fontWeightStrong:c,textColorCheckable:o,textColorHoverCheckable:o,textColorPressedCheckable:o,textColorChecked:C,colorCheckable:"#0000",colorHoverCheckable:ne,colorPressedCheckable:ie,colorChecked:l,colorCheckedHover:n,colorCheckedPressed:r,border:`1px solid ${w}`,textColor:o,color:T,colorBordered:"rgb(250, 250, 252)",closeIconColor:O,closeIconColorHover:m,closeIconColorPressed:b,closeColorHover:re,closeColorPressed:Q,borderPrimary:`1px solid ${q(l,{alpha:.3})}`,textColorPrimary:l,colorPrimary:q(l,{alpha:.12}),colorBorderedPrimary:q(l,{alpha:.1}),closeIconColorPrimary:l,closeIconColorHoverPrimary:l,closeIconColorPressedPrimary:l,closeColorHoverPrimary:q(l,{alpha:.12}),closeColorPressedPrimary:q(l,{alpha:.18}),borderInfo:`1px solid ${q(f,{alpha:.3})}`,textColorInfo:f,colorInfo:q(f,{alpha:.12}),colorBorderedInfo:q(f,{alpha:.1}),closeIconColorInfo:f,closeIconColorHoverInfo:f,closeIconColorPressedInfo:f,closeColorHoverInfo:q(f,{alpha:.12}),closeColorPressedInfo:q(f,{alpha:.18}),borderSuccess:`1px solid ${q(d,{alpha:.3})}`,textColorSuccess:d,colorSuccess:q(d,{alpha:.12}),colorBorderedSuccess:q(d,{alpha:.1}),closeIconColorSuccess:d,closeIconColorHoverSuccess:d,closeIconColorPressedSuccess:d,closeColorHoverSuccess:q(d,{alpha:.12}),closeColorPressedSuccess:q(d,{alpha:.18}),borderWarning:`1px solid ${q(i,{alpha:.35})}`,textColorWarning:i,colorWarning:q(i,{alpha:.15}),colorBorderedWarning:q(i,{alpha:.12}),closeIconColorWarning:i,closeIconColorHoverWarning:i,closeIconColorPressedWarning:i,closeColorHoverWarning:q(i,{alpha:.12}),closeColorPressedWarning:q(i,{alpha:.18}),borderError:`1px solid ${q(g,{alpha:.23})}`,textColorError:g,colorError:q(g,{alpha:.1}),colorBorderedError:q(g,{alpha:.08}),closeIconColorError:g,closeIconColorHoverError:g,closeIconColorPressedError:g,closeColorHoverError:q(g,{alpha:.12}),closeColorPressedError:q(g,{alpha:.18})})}const tn={common:So,self:en},on={color:Object,type:{type:String,default:"default"},round:Boolean,size:{type:String,default:"medium"},closable:Boolean,disabled:{type:Boolean,default:void 0}},nn=W("tag",`
 --n-close-margin: var(--n-close-margin-top) var(--n-close-margin-right) var(--n-close-margin-bottom) var(--n-close-margin-left);
 white-space: nowrap;
 position: relative;
 box-sizing: border-box;
 cursor: default;
 display: inline-flex;
 align-items: center;
 flex-wrap: nowrap;
 padding: var(--n-padding);
 border-radius: var(--n-border-radius);
 color: var(--n-text-color);
 background-color: var(--n-color);
 transition: 
 border-color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 line-height: 1;
 height: var(--n-height);
 font-size: var(--n-font-size);
`,[J("strong",`
 font-weight: var(--n-font-weight-strong);
 `),D("border",`
 pointer-events: none;
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
 border: var(--n-border);
 transition: border-color .3s var(--n-bezier);
 `),D("icon",`
 display: flex;
 margin: 0 4px 0 0;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 font-size: var(--n-avatar-size-override);
 `),D("avatar",`
 display: flex;
 margin: 0 6px 0 0;
 `),D("close",`
 margin: var(--n-close-margin);
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),J("round",`
 padding: 0 calc(var(--n-height) / 3);
 border-radius: calc(var(--n-height) / 2);
 `,[D("icon",`
 margin: 0 4px 0 calc((var(--n-height) - 8px) / -2);
 `),D("avatar",`
 margin: 0 6px 0 calc((var(--n-height) - 8px) / -2);
 `),J("closable",`
 padding: 0 calc(var(--n-height) / 4) 0 calc(var(--n-height) / 3);
 `)]),J("icon, avatar",[J("round",`
 padding: 0 calc(var(--n-height) / 3) 0 calc(var(--n-height) / 2);
 `)]),J("disabled",`
 cursor: not-allowed !important;
 opacity: var(--n-opacity-disabled);
 `),J("checkable",`
 cursor: pointer;
 box-shadow: none;
 color: var(--n-text-color-checkable);
 background-color: var(--n-color-checkable);
 `,[Fe("disabled",[de("&:hover","background-color: var(--n-color-hover-checkable);",[Fe("checked","color: var(--n-text-color-hover-checkable);")]),de("&:active","background-color: var(--n-color-pressed-checkable);",[Fe("checked","color: var(--n-text-color-pressed-checkable);")])]),J("checked",`
 color: var(--n-text-color-checked);
 background-color: var(--n-color-checked);
 `,[Fe("disabled",[de("&:hover","background-color: var(--n-color-checked-hover);"),de("&:active","background-color: var(--n-color-checked-pressed);")])])])]),ln=Object.assign(Object.assign(Object.assign({},xe.props),on),{bordered:{type:Boolean,default:void 0},checked:Boolean,checkable:Boolean,strong:Boolean,triggerClickOnClose:Boolean,onClose:[Array,Function],onMouseenter:Function,onMouseleave:Function,"onUpdate:checked":Function,onUpdateChecked:Function,internalCloseFocusable:{type:Boolean,default:!0},internalCloseIsButtonTag:{type:Boolean,default:!0},onCheckedChange:Function}),rn=To("n-tag"),at=ge({name:"Tag",props:ln,slots:Object,setup(e){const o=P(null),{mergedBorderedRef:n,mergedClsPrefixRef:r,inlineThemeDisabled:l,mergedRtlRef:f,mergedComponentPropsRef:d}=Qe(e),i=A(()=>{var b,F;return e.size||((F=(b=d?.value)===null||b===void 0?void 0:b.Tag)===null||F===void 0?void 0:F.size)||"medium"}),g=xe("Tag","-tag",nn,tn,e,r);Xe(rn,{roundRef:oe(e,"round")});function C(){if(!e.disabled&&e.checkable){const{checked:b,onCheckedChange:F,onUpdateChecked:R,"onUpdate:checked":y}=e;R&&R(!b),y&&y(!b),F&&F(!b)}}function w(b){if(e.triggerClickOnClose||b.stopPropagation(),!e.disabled){const{onClose:F}=e;F&&ve(F,b)}}const S={setTextContent(b){const{value:F}=o;F&&(F.textContent=b)}},T=vt("Tag",f,r),O=A(()=>{const{type:b,color:{color:F,textColor:R}={}}=e,y=i.value,{common:{cubicBezierEaseInOut:I},self:{padding:_,closeMargin:L,borderRadius:Y,opacityDisabled:j,textColorCheckable:le,textColorHoverCheckable:re,textColorPressedCheckable:Q,textColorChecked:ne,colorCheckable:ie,colorHoverCheckable:c,colorPressedCheckable:x,colorChecked:N,colorCheckedHover:z,colorCheckedPressed:X,closeBorderRadius:H,fontWeightStrong:K,[ee("colorBordered",b)]:V,[ee("closeSize",y)]:U,[ee("closeIconSize",y)]:ae,[ee("fontSize",y)]:se,[ee("height",y)]:a,[ee("color",b)]:v,[ee("textColor",b)]:G,[ee("border",b)]:fe,[ee("closeIconColor",b)]:he,[ee("closeIconColorHover",b)]:ce,[ee("closeIconColorPressed",b)]:we,[ee("closeColorHover",b)]:be,[ee("closeColorPressed",b)]:Se}}=g.value,pe=Me(L);return{"--n-font-weight-strong":K,"--n-avatar-size-override":`calc(${a} - 8px)`,"--n-bezier":I,"--n-border-radius":Y,"--n-border":fe,"--n-close-icon-size":ae,"--n-close-color-pressed":Se,"--n-close-color-hover":be,"--n-close-border-radius":H,"--n-close-icon-color":he,"--n-close-icon-color-hover":ce,"--n-close-icon-color-pressed":we,"--n-close-icon-color-disabled":he,"--n-close-margin-top":pe.top,"--n-close-margin-right":pe.right,"--n-close-margin-bottom":pe.bottom,"--n-close-margin-left":pe.left,"--n-close-size":U,"--n-color":F||(n.value?V:v),"--n-color-checkable":ie,"--n-color-checked":N,"--n-color-checked-hover":z,"--n-color-checked-pressed":X,"--n-color-hover-checkable":c,"--n-color-pressed-checkable":x,"--n-font-size":se,"--n-height":a,"--n-opacity-disabled":j,"--n-padding":_,"--n-text-color":R||G,"--n-text-color-checkable":le,"--n-text-color-checked":ne,"--n-text-color-hover-checkable":re,"--n-text-color-pressed-checkable":Q}}),m=l?Ze("tag",A(()=>{let b="";const{type:F,color:{color:R,textColor:y}={}}=e;return b+=F[0],b+=i.value[0],R&&(b+=`a${bt(R)}`),y&&(b+=`b${bt(y)}`),n.value&&(b+="c"),b}),O,e):void 0;return Object.assign(Object.assign({},S),{rtlEnabled:T,mergedClsPrefix:r,contentRef:o,mergedBordered:n,handleClick:C,handleCloseClick:w,cssVars:l?void 0:O,themeClass:m?.themeClass,onRender:m?.onRender})},render(){var e,o;const{mergedClsPrefix:n,rtlEnabled:r,closable:l,color:{borderColor:f}={},round:d,onRender:i,$slots:g}=this;i?.();const C=Ye(g.avatar,S=>S&&u("div",{class:`${n}-tag__avatar`},S)),w=Ye(g.icon,S=>S&&u("div",{class:`${n}-tag__icon`},S));return u("div",{class:[`${n}-tag`,this.themeClass,{[`${n}-tag--rtl`]:r,[`${n}-tag--strong`]:this.strong,[`${n}-tag--disabled`]:this.disabled,[`${n}-tag--checkable`]:this.checkable,[`${n}-tag--checked`]:this.checkable&&this.checked,[`${n}-tag--round`]:d,[`${n}-tag--avatar`]:C,[`${n}-tag--icon`]:w,[`${n}-tag--closable`]:l}],style:this.cssVars,onClick:this.handleClick,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},w||C,u("span",{class:`${n}-tag__content`,ref:"contentRef"},(o=(e=this.$slots).default)===null||o===void 0?void 0:o.call(e)),!this.checkable&&l?u(ko,{clsPrefix:n,class:`${n}-tag__close`,disabled:this.disabled,onClick:this.handleCloseClick,focusable:this.internalCloseFocusable,round:d,isButtonTag:this.internalCloseIsButtonTag,absolute:!0}):null,!this.checkable&&this.mergedBordered?u("div",{class:`${n}-tag__border`,style:{borderColor:f}}):null)}}),an=de([W("base-selection",`
 --n-padding-single: var(--n-padding-single-top) var(--n-padding-single-right) var(--n-padding-single-bottom) var(--n-padding-single-left);
 --n-padding-multiple: var(--n-padding-multiple-top) var(--n-padding-multiple-right) var(--n-padding-multiple-bottom) var(--n-padding-multiple-left);
 position: relative;
 z-index: auto;
 box-shadow: none;
 width: 100%;
 max-width: 100%;
 display: inline-block;
 vertical-align: bottom;
 border-radius: var(--n-border-radius);
 min-height: var(--n-height);
 line-height: 1.5;
 font-size: var(--n-font-size);
 `,[W("base-loading",`
 color: var(--n-loading-color);
 `),W("base-selection-tags","min-height: var(--n-height);"),D("border, state-border",`
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 pointer-events: none;
 border: var(--n-border);
 border-radius: inherit;
 transition:
 box-shadow .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `),D("state-border",`
 z-index: 1;
 border-color: #0000;
 `),W("base-suffix",`
 cursor: pointer;
 position: absolute;
 top: 50%;
 transform: translateY(-50%);
 right: 10px;
 `,[D("arrow",`
 font-size: var(--n-arrow-size);
 color: var(--n-arrow-color);
 transition: color .3s var(--n-bezier);
 `)]),W("base-selection-overlay",`
 display: flex;
 align-items: center;
 white-space: nowrap;
 pointer-events: none;
 position: absolute;
 top: 0;
 right: 0;
 bottom: 0;
 left: 0;
 padding: var(--n-padding-single);
 transition: color .3s var(--n-bezier);
 `,[D("wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 overflow: hidden;
 text-overflow: ellipsis;
 `)]),W("base-selection-placeholder",`
 color: var(--n-placeholder-color);
 `,[D("inner",`
 max-width: 100%;
 overflow: hidden;
 `)]),W("base-selection-tags",`
 cursor: pointer;
 outline: none;
 box-sizing: border-box;
 position: relative;
 z-index: auto;
 display: flex;
 padding: var(--n-padding-multiple);
 flex-wrap: wrap;
 align-items: center;
 width: 100%;
 vertical-align: bottom;
 background-color: var(--n-color);
 border-radius: inherit;
 transition:
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),W("base-selection-label",`
 height: var(--n-height);
 display: inline-flex;
 width: 100%;
 vertical-align: bottom;
 cursor: pointer;
 outline: none;
 z-index: auto;
 box-sizing: border-box;
 position: relative;
 transition:
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 border-radius: inherit;
 background-color: var(--n-color);
 align-items: center;
 `,[W("base-selection-input",`
 font-size: inherit;
 line-height: inherit;
 outline: none;
 cursor: pointer;
 box-sizing: border-box;
 border:none;
 width: 100%;
 padding: var(--n-padding-single);
 background-color: #0000;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 caret-color: var(--n-caret-color);
 `,[D("content",`
 text-overflow: ellipsis;
 overflow: hidden;
 white-space: nowrap; 
 `)]),D("render-label",`
 color: var(--n-text-color);
 `)]),Fe("disabled",[de("&:hover",[D("state-border",`
 box-shadow: var(--n-box-shadow-hover);
 border: var(--n-border-hover);
 `)]),J("focus",[D("state-border",`
 box-shadow: var(--n-box-shadow-focus);
 border: var(--n-border-focus);
 `)]),J("active",[D("state-border",`
 box-shadow: var(--n-box-shadow-active);
 border: var(--n-border-active);
 `),W("base-selection-label","background-color: var(--n-color-active);"),W("base-selection-tags","background-color: var(--n-color-active);")])]),J("disabled","cursor: not-allowed;",[D("arrow",`
 color: var(--n-arrow-color-disabled);
 `),W("base-selection-label",`
 cursor: not-allowed;
 background-color: var(--n-color-disabled);
 `,[W("base-selection-input",`
 cursor: not-allowed;
 color: var(--n-text-color-disabled);
 `),D("render-label",`
 color: var(--n-text-color-disabled);
 `)]),W("base-selection-tags",`
 cursor: not-allowed;
 background-color: var(--n-color-disabled);
 `),W("base-selection-placeholder",`
 cursor: not-allowed;
 color: var(--n-placeholder-color-disabled);
 `)]),W("base-selection-input-tag",`
 height: calc(var(--n-height) - 6px);
 line-height: calc(var(--n-height) - 6px);
 outline: none;
 display: none;
 position: relative;
 margin-bottom: 3px;
 max-width: 100%;
 vertical-align: bottom;
 `,[D("input",`
 font-size: inherit;
 font-family: inherit;
 min-width: 1px;
 padding: 0;
 background-color: #0000;
 outline: none;
 border: none;
 max-width: 100%;
 overflow: hidden;
 width: 1em;
 line-height: inherit;
 cursor: pointer;
 color: var(--n-text-color);
 caret-color: var(--n-caret-color);
 `),D("mirror",`
 position: absolute;
 left: 0;
 top: 0;
 white-space: pre;
 visibility: hidden;
 user-select: none;
 -webkit-user-select: none;
 opacity: 0;
 `)]),["warning","error"].map(e=>J(`${e}-status`,[D("state-border",`border: var(--n-border-${e});`),Fe("disabled",[de("&:hover",[D("state-border",`
 box-shadow: var(--n-box-shadow-hover-${e});
 border: var(--n-border-hover-${e});
 `)]),J("active",[D("state-border",`
 box-shadow: var(--n-box-shadow-active-${e});
 border: var(--n-border-active-${e});
 `),W("base-selection-label",`background-color: var(--n-color-active-${e});`),W("base-selection-tags",`background-color: var(--n-color-active-${e});`)]),J("focus",[D("state-border",`
 box-shadow: var(--n-box-shadow-focus-${e});
 border: var(--n-border-focus-${e});
 `)])])]))]),W("base-selection-popover",`
 margin-bottom: -3px;
 display: flex;
 flex-wrap: wrap;
 margin-right: -8px;
 `),W("base-selection-tag-wrapper",`
 max-width: 100%;
 display: inline-flex;
 padding: 0 7px 3px 0;
 `,[de("&:last-child","padding-right: 0;"),W("tag",`
 font-size: 14px;
 max-width: 100%;
 `,[D("content",`
 line-height: 1.25;
 text-overflow: ellipsis;
 overflow: hidden;
 `)])])]),sn=ge({name:"InternalSelection",props:Object.assign(Object.assign({},xe.props),{clsPrefix:{type:String,required:!0},bordered:{type:Boolean,default:void 0},active:Boolean,pattern:{type:String,default:""},placeholder:String,selectedOption:{type:Object,default:null},selectedOptions:{type:Array,default:null},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},multiple:Boolean,filterable:Boolean,clearable:Boolean,disabled:Boolean,size:{type:String,default:"medium"},loading:Boolean,autofocus:Boolean,showArrow:{type:Boolean,default:!0},inputProps:Object,focused:Boolean,renderTag:Function,onKeydown:Function,onClick:Function,onBlur:Function,onFocus:Function,onDeleteOption:Function,maxTagCount:[String,Number],ellipsisTagPopoverProps:Object,onClear:Function,onPatternInput:Function,onPatternFocus:Function,onPatternBlur:Function,renderLabel:Function,status:String,inlineThemeDisabled:Boolean,ignoreComposition:{type:Boolean,default:!0},onResize:Function}),setup(e){const{mergedClsPrefixRef:o,mergedRtlRef:n}=Qe(e),r=vt("InternalSelection",n,o),l=P(null),f=P(null),d=P(null),i=P(null),g=P(null),C=P(null),w=P(null),S=P(null),T=P(null),O=P(null),m=P(!1),b=P(!1),F=P(!1),R=xe("InternalSelection","-internal-selection",an,Po,e,oe(e,"clsPrefix")),y=A(()=>e.clearable&&!e.disabled&&(F.value||e.active)),I=A(()=>e.selectedOption?e.renderTag?e.renderTag({option:e.selectedOption,handleClose:()=>{}}):e.renderLabel?e.renderLabel(e.selectedOption,!0):ze(e.selectedOption[e.labelField],e.selectedOption,!0):e.placeholder),_=A(()=>{const s=e.selectedOption;if(s)return s[e.labelField]}),L=A(()=>e.multiple?!!(Array.isArray(e.selectedOptions)&&e.selectedOptions.length):e.selectedOption!==null);function Y(){var s;const{value:p}=l;if(p){const{value:Z}=f;Z&&(Z.style.width=`${p.offsetWidth}px`,e.maxTagCount!=="responsive"&&((s=T.value)===null||s===void 0||s.sync({showAllItemsBeforeCalculate:!1})))}}function j(){const{value:s}=O;s&&(s.style.display="none")}function le(){const{value:s}=O;s&&(s.style.display="inline-block")}Oe(oe(e,"active"),s=>{s||j()}),Oe(oe(e,"pattern"),()=>{e.multiple&&ft(Y)});function re(s){const{onFocus:p}=e;p&&p(s)}function Q(s){const{onBlur:p}=e;p&&p(s)}function ne(s){const{onDeleteOption:p}=e;p&&p(s)}function ie(s){const{onClear:p}=e;p&&p(s)}function c(s){const{onPatternInput:p}=e;p&&p(s)}function x(s){var p;(!s.relatedTarget||!(!((p=d.value)===null||p===void 0)&&p.contains(s.relatedTarget)))&&re(s)}function N(s){var p;!((p=d.value)===null||p===void 0)&&p.contains(s.relatedTarget)||Q(s)}function z(s){ie(s)}function X(){F.value=!0}function H(){F.value=!1}function K(s){!e.active||!e.filterable||s.target!==f.value&&s.preventDefault()}function V(s){ne(s)}const U=P(!1);function ae(s){if(s.key==="Backspace"&&!U.value&&!e.pattern.length){const{selectedOptions:p}=e;p?.length&&V(p[p.length-1])}}let se=null;function a(s){const{value:p}=l;if(p){const Z=s.target.value;p.textContent=Z,Y()}e.ignoreComposition&&U.value?se=s:c(s)}function v(){U.value=!0}function G(){U.value=!1,e.ignoreComposition&&c(se),se=null}function fe(s){var p;b.value=!0,(p=e.onPatternFocus)===null||p===void 0||p.call(e,s)}function he(s){var p;b.value=!1,(p=e.onPatternBlur)===null||p===void 0||p.call(e,s)}function ce(){var s,p;if(e.filterable)b.value=!1,(s=C.value)===null||s===void 0||s.blur(),(p=f.value)===null||p===void 0||p.blur();else if(e.multiple){const{value:Z}=i;Z?.blur()}else{const{value:Z}=g;Z?.blur()}}function we(){var s,p,Z;e.filterable?(b.value=!1,(s=C.value)===null||s===void 0||s.focus()):e.multiple?(p=i.value)===null||p===void 0||p.focus():(Z=g.value)===null||Z===void 0||Z.focus()}function be(){const{value:s}=f;s&&(le(),s.focus())}function Se(){const{value:s}=f;s&&s.blur()}function pe(s){const{value:p}=w;p&&p.setTextContent(`+${s}`)}function Be(){const{value:s}=S;return s}function _e(){return f.value}let Re=null;function ke(){Re!==null&&window.clearTimeout(Re)}function $e(){e.active||(ke(),Re=window.setTimeout(()=>{L.value&&(m.value=!0)},100))}function Ee(){ke()}function Ae(s){s||(ke(),m.value=!1)}Oe(L,s=>{s||(m.value=!1)}),Le(()=>{ho(()=>{const s=C.value;s&&(e.disabled?s.removeAttribute("tabindex"):s.tabIndex=b.value?-1:0)})}),Bt(d,e.onResize);const{inlineThemeDisabled:Pe}=e,Te=A(()=>{const{size:s}=e,{common:{cubicBezierEaseInOut:p},self:{fontWeight:Z,borderRadius:et,color:tt,placeholderColor:De,textColor:He,paddingSingle:je,paddingMultiple:ot,caretColor:nt,colorDisabled:Ve,textColorDisabled:ye,placeholderColorDisabled:t,colorActive:h,boxShadowFocus:k,boxShadowActive:$,boxShadowHover:M,border:B,borderFocus:E,borderHover:te,borderActive:me,arrowColor:$t,arrowColorDisabled:Et,loadingColor:At,colorActiveWarning:Nt,boxShadowFocusWarning:Wt,boxShadowActiveWarning:Lt,boxShadowHoverWarning:Dt,borderWarning:Ht,borderFocusWarning:jt,borderHoverWarning:Vt,borderActiveWarning:Kt,colorActiveError:Ut,boxShadowFocusError:qt,boxShadowActiveError:Gt,boxShadowHoverError:Xt,borderError:Yt,borderFocusError:Jt,borderHoverError:Qt,borderActiveError:Zt,clearColor:eo,clearColorHover:to,clearColorPressed:oo,clearSize:no,arrowSize:lo,[ee("height",s)]:ro,[ee("fontSize",s)]:io}}=R.value,Ke=Me(je),Ue=Me(ot);return{"--n-bezier":p,"--n-border":B,"--n-border-active":me,"--n-border-focus":E,"--n-border-hover":te,"--n-border-radius":et,"--n-box-shadow-active":$,"--n-box-shadow-focus":k,"--n-box-shadow-hover":M,"--n-caret-color":nt,"--n-color":tt,"--n-color-active":h,"--n-color-disabled":Ve,"--n-font-size":io,"--n-height":ro,"--n-padding-single-top":Ke.top,"--n-padding-multiple-top":Ue.top,"--n-padding-single-right":Ke.right,"--n-padding-multiple-right":Ue.right,"--n-padding-single-left":Ke.left,"--n-padding-multiple-left":Ue.left,"--n-padding-single-bottom":Ke.bottom,"--n-padding-multiple-bottom":Ue.bottom,"--n-placeholder-color":De,"--n-placeholder-color-disabled":t,"--n-text-color":He,"--n-text-color-disabled":ye,"--n-arrow-color":$t,"--n-arrow-color-disabled":Et,"--n-loading-color":At,"--n-color-active-warning":Nt,"--n-box-shadow-focus-warning":Wt,"--n-box-shadow-active-warning":Lt,"--n-box-shadow-hover-warning":Dt,"--n-border-warning":Ht,"--n-border-focus-warning":jt,"--n-border-hover-warning":Vt,"--n-border-active-warning":Kt,"--n-color-active-error":Ut,"--n-box-shadow-focus-error":qt,"--n-box-shadow-active-error":Gt,"--n-box-shadow-hover-error":Xt,"--n-border-error":Yt,"--n-border-focus-error":Jt,"--n-border-hover-error":Qt,"--n-border-active-error":Zt,"--n-clear-size":no,"--n-clear-color":eo,"--n-clear-color-hover":to,"--n-clear-color-pressed":oo,"--n-arrow-size":lo,"--n-font-weight":Z}}),ue=Pe?Ze("internal-selection",A(()=>e.size[0]),Te,e):void 0;return{mergedTheme:R,mergedClearable:y,mergedClsPrefix:o,rtlEnabled:r,patternInputFocused:b,filterablePlaceholder:I,label:_,selected:L,showTagsPanel:m,isComposing:U,counterRef:w,counterWrapperRef:S,patternInputMirrorRef:l,patternInputRef:f,selfRef:d,multipleElRef:i,singleElRef:g,patternInputWrapperRef:C,overflowRef:T,inputTagElRef:O,handleMouseDown:K,handleFocusin:x,handleClear:z,handleMouseEnter:X,handleMouseLeave:H,handleDeleteOption:V,handlePatternKeyDown:ae,handlePatternInputInput:a,handlePatternInputBlur:he,handlePatternInputFocus:fe,handleMouseEnterCounter:$e,handleMouseLeaveCounter:Ee,handleFocusout:N,handleCompositionEnd:G,handleCompositionStart:v,onPopoverUpdateShow:Ae,focus:we,focusInput:be,blur:ce,blurInput:Se,updateCounter:pe,getCounter:Be,getTail:_e,renderLabel:e.renderLabel,cssVars:Pe?void 0:Te,themeClass:ue?.themeClass,onRender:ue?.onRender}},render(){const{status:e,multiple:o,size:n,disabled:r,filterable:l,maxTagCount:f,bordered:d,clsPrefix:i,ellipsisTagPopoverProps:g,onRender:C,renderTag:w,renderLabel:S}=this;C?.();const T=f==="responsive",O=typeof f=="number",m=T||O,b=u(Fo,null,{default:()=>u(Oo,{clsPrefix:i,loading:this.loading,showArrow:this.showArrow,showClear:this.mergedClearable&&this.selected,onClear:this.handleClear},{default:()=>{var R,y;return(y=(R=this.$slots).arrow)===null||y===void 0?void 0:y.call(R)}})});let F;if(o){const{labelField:R}=this,y=c=>u("div",{class:`${i}-base-selection-tag-wrapper`,key:c.value},w?w({option:c,handleClose:()=>{this.handleDeleteOption(c)}}):u(at,{size:n,closable:!c.disabled,disabled:r,onClose:()=>{this.handleDeleteOption(c)},internalCloseIsButtonTag:!1,internalCloseFocusable:!1},{default:()=>S?S(c,!0):ze(c[R],c,!0)})),I=()=>(O?this.selectedOptions.slice(0,f):this.selectedOptions).map(y),_=l?u("div",{class:`${i}-base-selection-input-tag`,ref:"inputTagElRef",key:"__input-tag__"},u("input",Object.assign({},this.inputProps,{ref:"patternInputRef",tabindex:-1,disabled:r,value:this.pattern,autofocus:this.autofocus,class:`${i}-base-selection-input-tag__input`,onBlur:this.handlePatternInputBlur,onFocus:this.handlePatternInputFocus,onKeydown:this.handlePatternKeyDown,onInput:this.handlePatternInputInput,onCompositionstart:this.handleCompositionStart,onCompositionend:this.handleCompositionEnd})),u("span",{ref:"patternInputMirrorRef",class:`${i}-base-selection-input-tag__mirror`},this.pattern)):null,L=T?()=>u("div",{class:`${i}-base-selection-tag-wrapper`,ref:"counterWrapperRef"},u(at,{size:n,ref:"counterRef",onMouseenter:this.handleMouseEnterCounter,onMouseleave:this.handleMouseLeaveCounter,disabled:r})):void 0;let Y;if(O){const c=this.selectedOptions.length-f;c>0&&(Y=u("div",{class:`${i}-base-selection-tag-wrapper`,key:"__counter__"},u(at,{size:n,ref:"counterRef",onMouseenter:this.handleMouseEnterCounter,disabled:r},{default:()=>`+${c}`})))}const j=T?l?u(xt,{ref:"overflowRef",updateCounter:this.updateCounter,getCounter:this.getCounter,getTail:this.getTail,style:{width:"100%",display:"flex",overflow:"hidden"}},{default:I,counter:L,tail:()=>_}):u(xt,{ref:"overflowRef",updateCounter:this.updateCounter,getCounter:this.getCounter,style:{width:"100%",display:"flex",overflow:"hidden"}},{default:I,counter:L}):O&&Y?I().concat(Y):I(),le=m?()=>u("div",{class:`${i}-base-selection-popover`},T?I():this.selectedOptions.map(y)):void 0,re=m?Object.assign({show:this.showTagsPanel,trigger:"hover",overlap:!0,placement:"top",width:"trigger",onUpdateShow:this.onPopoverUpdateShow,theme:this.mergedTheme.peers.Popover,themeOverrides:this.mergedTheme.peerOverrides.Popover},g):null,ne=(this.selected?!1:this.active?!this.pattern&&!this.isComposing:!0)?u("div",{class:`${i}-base-selection-placeholder ${i}-base-selection-overlay`},u("div",{class:`${i}-base-selection-placeholder__inner`},this.placeholder)):null,ie=l?u("div",{ref:"patternInputWrapperRef",class:`${i}-base-selection-tags`},j,T?null:_,b):u("div",{ref:"multipleElRef",class:`${i}-base-selection-tags`,tabindex:r?void 0:0},j,b);F=u(fo,null,m?u(Io,Object.assign({},re,{scrollable:!0,style:"max-height: calc(var(--v-target-height) * 6.6);"}),{trigger:()=>ie,default:le}):ie,ne)}else if(l){const R=this.pattern||this.isComposing,y=this.active?!R:!this.selected,I=this.active?!1:this.selected;F=u("div",{ref:"patternInputWrapperRef",class:`${i}-base-selection-label`,title:this.patternInputFocused?void 0:St(this.label)},u("input",Object.assign({},this.inputProps,{ref:"patternInputRef",class:`${i}-base-selection-input`,value:this.active?this.pattern:"",placeholder:"",readonly:r,disabled:r,tabindex:-1,autofocus:this.autofocus,onFocus:this.handlePatternInputFocus,onBlur:this.handlePatternInputBlur,onInput:this.handlePatternInputInput,onCompositionstart:this.handleCompositionStart,onCompositionend:this.handleCompositionEnd})),I?u("div",{class:`${i}-base-selection-label__render-label ${i}-base-selection-overlay`,key:"input"},u("div",{class:`${i}-base-selection-overlay__wrapper`},w?w({option:this.selectedOption,handleClose:()=>{}}):S?S(this.selectedOption,!0):ze(this.label,this.selectedOption,!0))):null,y?u("div",{class:`${i}-base-selection-placeholder ${i}-base-selection-overlay`,key:"placeholder"},u("div",{class:`${i}-base-selection-overlay__wrapper`},this.filterablePlaceholder)):null,b)}else F=u("div",{ref:"singleElRef",class:`${i}-base-selection-label`,tabindex:this.disabled?void 0:0},this.label!==void 0?u("div",{class:`${i}-base-selection-input`,title:St(this.label),key:"input"},u("div",{class:`${i}-base-selection-input__content`},w?w({option:this.selectedOption,handleClose:()=>{}}):S?S(this.selectedOption,!0):ze(this.label,this.selectedOption,!0))):u("div",{class:`${i}-base-selection-placeholder ${i}-base-selection-overlay`,key:"placeholder"},u("div",{class:`${i}-base-selection-placeholder__inner`},this.placeholder)),b);return u("div",{ref:"selfRef",class:[`${i}-base-selection`,this.rtlEnabled&&`${i}-base-selection--rtl`,this.themeClass,e&&`${i}-base-selection--${e}-status`,{[`${i}-base-selection--active`]:this.active,[`${i}-base-selection--selected`]:this.selected||this.active&&this.pattern,[`${i}-base-selection--disabled`]:this.disabled,[`${i}-base-selection--multiple`]:this.multiple,[`${i}-base-selection--focus`]:this.focused}],style:this.cssVars,onClick:this.onClick,onMouseenter:this.handleMouseEnter,onMouseleave:this.handleMouseLeave,onKeydown:this.onKeydown,onFocusin:this.handleFocusin,onFocusout:this.handleFocusout,onMousedown:this.handleMouseDown},F,d?u("div",{class:`${i}-base-selection__border`}):null,d?u("div",{class:`${i}-base-selection__state-border`}):null)}});function Je(e){return e.type==="group"}function _t(e){return e.type==="ignored"}function st(e,o){try{return!!(1+o.toString().toLowerCase().indexOf(e.trim().toLowerCase()))}catch{return!1}}function cn(e,o){return{getIsGroup:Je,getIgnored:_t,getKey(r){return Je(r)?r.name||r.key||"key-required":r[e]},getChildren(r){return r[o]}}}function dn(e,o,n,r){if(!o)return e;function l(f){if(!Array.isArray(f))return[];const d=[];for(const i of f)if(Je(i)){const g=l(i[r]);g.length&&d.push(Object.assign({},i,{[r]:g}))}else{if(_t(i))continue;o(n,i)&&d.push(i)}return d}return l(e)}function un(e,o,n){const r=new Map;return e.forEach(l=>{Je(l)?l[n].forEach(f=>{r.set(f[o],f)}):r.set(l[o],l)}),r}const fn=de([W("select",`
 z-index: auto;
 outline: none;
 width: 100%;
 position: relative;
 font-weight: var(--n-font-weight);
 `),W("select-menu",`
 margin: 4px 0;
 box-shadow: var(--n-menu-box-shadow);
 `,[Pt({originalTransition:"background-color .3s var(--n-bezier), box-shadow .3s var(--n-bezier)"})])]),hn=Object.assign(Object.assign({},xe.props),{to:dt.propTo,bordered:{type:Boolean,default:void 0},clearable:Boolean,clearCreatedOptionsOnClear:{type:Boolean,default:!0},clearFilterAfterSelect:{type:Boolean,default:!0},options:{type:Array,default:()=>[]},defaultValue:{type:[String,Number,Array],default:null},keyboard:{type:Boolean,default:!0},value:[String,Number,Array],placeholder:String,menuProps:Object,multiple:Boolean,size:String,menuSize:{type:String},filterable:Boolean,disabled:{type:Boolean,default:void 0},remote:Boolean,loading:Boolean,filter:Function,placement:{type:String,default:"bottom-start"},widthMode:{type:String,default:"trigger"},tag:Boolean,onCreate:Function,fallbackOption:{type:[Function,Boolean],default:void 0},show:{type:Boolean,default:void 0},showArrow:{type:Boolean,default:!0},maxTagCount:[Number,String],ellipsisTagPopoverProps:Object,consistentMenuWidth:{type:Boolean,default:!0},virtualScroll:{type:Boolean,default:!0},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},childrenField:{type:String,default:"children"},renderLabel:Function,renderOption:Function,renderTag:Function,"onUpdate:value":[Function,Array],inputProps:Object,nodeProps:Function,ignoreComposition:{type:Boolean,default:!0},showOnFocus:Boolean,onUpdateValue:[Function,Array],onBlur:[Function,Array],onClear:[Function,Array],onFocus:[Function,Array],onScroll:[Function,Array],onSearch:[Function,Array],onUpdateShow:[Function,Array],"onUpdate:show":[Function,Array],displayDirective:{type:String,default:"show"},resetMenuOnOptionsChange:{type:Boolean,default:!0},status:String,showCheckmark:{type:Boolean,default:!0},scrollbarProps:Object,onChange:[Function,Array],items:Array}),mn=ge({name:"Select",props:hn,slots:Object,setup(e){const{mergedClsPrefixRef:o,mergedBorderedRef:n,namespaceRef:r,inlineThemeDisabled:l}=Qe(e),f=xe("Select","-select",fn,Lo,e,o),d=P(e.defaultValue),i=oe(e,"value"),g=mt(i,d),C=P(!1),w=P(""),S=Wo(e,["items","options"]),T=P([]),O=P([]),m=A(()=>O.value.concat(T.value).concat(S.value)),b=A(()=>{const{filter:t}=e;if(t)return t;const{labelField:h,valueField:k}=e;return($,M)=>{if(!M)return!1;const B=M[h];if(typeof B=="string")return st($,B);const E=M[k];return typeof E=="string"?st($,E):typeof E=="number"?st($,String(E)):!1}}),F=A(()=>{if(e.remote)return S.value;{const{value:t}=m,{value:h}=w;return!h.length||!e.filterable?t:dn(t,b.value,h,e.childrenField)}}),R=A(()=>{const{valueField:t,childrenField:h}=e,k=cn(t,h);return jo(F.value,k)}),y=A(()=>un(m.value,e.valueField,e.childrenField)),I=P(!1),_=mt(oe(e,"show"),I),L=P(null),Y=P(null),j=P(null),{localeRef:le}=_o("Select"),re=A(()=>{var t;return(t=e.placeholder)!==null&&t!==void 0?t:le.value.placeholder}),Q=[],ne=P(new Map),ie=A(()=>{const{fallbackOption:t}=e;if(t===void 0){const{labelField:h,valueField:k}=e;return $=>({[h]:String($),[k]:$})}return t===!1?!1:h=>Object.assign(t(h),{value:h})});function c(t){const h=e.remote,{value:k}=ne,{value:$}=y,{value:M}=ie,B=[];return t.forEach(E=>{if($.has(E))B.push($.get(E));else if(h&&k.has(E))B.push(k.get(E));else if(M){const te=M(E);te&&B.push(te)}}),B}const x=A(()=>{if(e.multiple){const{value:t}=g;return Array.isArray(t)?c(t):[]}return null}),N=A(()=>{const{value:t}=g;return!e.multiple&&!Array.isArray(t)?t===null?null:c([t])[0]||null:null}),z=$o(e),{mergedSizeRef:X,mergedDisabledRef:H,mergedStatusRef:K}=z;function V(t,h){const{onChange:k,"onUpdate:value":$,onUpdateValue:M}=e,{nTriggerFormChange:B,nTriggerFormInput:E}=z;k&&ve(k,t,h),M&&ve(M,t,h),$&&ve($,t,h),d.value=t,B(),E()}function U(t){const{onBlur:h}=e,{nTriggerFormBlur:k}=z;h&&ve(h,t),k()}function ae(){const{onClear:t}=e;t&&ve(t)}function se(t){const{onFocus:h,showOnFocus:k}=e,{nTriggerFormFocus:$}=z;h&&ve(h,t),$(),k&&he()}function a(t){const{onSearch:h}=e;h&&ve(h,t)}function v(t){const{onScroll:h}=e;h&&ve(h,t)}function G(){var t;const{remote:h,multiple:k}=e;if(h){const{value:$}=ne;if(k){const{valueField:M}=e;(t=x.value)===null||t===void 0||t.forEach(B=>{$.set(B[M],B)})}else{const M=N.value;M&&$.set(M[e.valueField],M)}}}function fe(t){const{onUpdateShow:h,"onUpdate:show":k}=e;h&&ve(h,t),k&&ve(k,t),I.value=t}function he(){H.value||(fe(!0),I.value=!0,e.filterable&&je())}function ce(){fe(!1)}function we(){w.value="",O.value=Q}const be=P(!1);function Se(){e.filterable&&(be.value=!0)}function pe(){e.filterable&&(be.value=!1,_.value||we())}function Be(){H.value||(_.value?e.filterable?je():ce():he())}function _e(t){var h,k;!((k=(h=j.value)===null||h===void 0?void 0:h.selfRef)===null||k===void 0)&&k.contains(t.relatedTarget)||(C.value=!1,U(t),ce())}function Re(t){se(t),C.value=!0}function ke(){C.value=!0}function $e(t){var h;!((h=L.value)===null||h===void 0)&&h.$el.contains(t.relatedTarget)||(C.value=!1,U(t),ce())}function Ee(){var t;(t=L.value)===null||t===void 0||t.focus(),ce()}function Ae(t){var h;_.value&&(!((h=L.value)===null||h===void 0)&&h.$el.contains(Ao(t))||ce())}function Pe(t){if(!Array.isArray(t))return[];if(ie.value)return Array.from(t);{const{remote:h}=e,{value:k}=y;if(h){const{value:$}=ne;return t.filter(M=>k.has(M)||$.has(M))}else return t.filter($=>k.has($))}}function Te(t){ue(t.rawNode)}function ue(t){if(H.value)return;const{tag:h,remote:k,clearFilterAfterSelect:$,valueField:M}=e;if(h&&!k){const{value:B}=O,E=B[0]||null;if(E){const te=T.value;te.length?te.push(E):T.value=[E],O.value=Q}}if(k&&ne.value.set(t[M],t),e.multiple){const B=Pe(g.value),E=B.findIndex(te=>te===t[M]);if(~E){if(B.splice(E,1),h&&!k){const te=s(t[M]);~te&&(T.value.splice(te,1),$&&(w.value=""))}}else B.push(t[M]),$&&(w.value="");V(B,c(B))}else{if(h&&!k){const B=s(t[M]);~B?T.value=[T.value[B]]:T.value=Q}He(),ce(),V(t[M],t)}}function s(t){return T.value.findIndex(k=>k[e.valueField]===t)}function p(t){_.value||he();const{value:h}=t.target;w.value=h;const{tag:k,remote:$}=e;if(a(h),k&&!$){if(!h){O.value=Q;return}const{onCreate:M}=e,B=M?M(h):{[e.labelField]:h,[e.valueField]:h},{valueField:E,labelField:te}=e;S.value.some(me=>me[E]===B[E]||me[te]===B[te])||T.value.some(me=>me[E]===B[E]||me[te]===B[te])?O.value=Q:O.value=[B]}}function Z(t){t.stopPropagation();const{multiple:h,tag:k,remote:$,clearCreatedOptionsOnClear:M}=e;!h&&e.filterable&&ce(),k&&!$&&M&&(T.value=Q),ae(),h?V([],[]):V(null,null)}function et(t){!We(t,"action")&&!We(t,"empty")&&!We(t,"header")&&t.preventDefault()}function tt(t){v(t)}function De(t){var h,k,$,M,B;if(!e.keyboard){t.preventDefault();return}switch(t.key){case" ":if(e.filterable)break;t.preventDefault();case"Enter":if(!(!((h=L.value)===null||h===void 0)&&h.isComposing)){if(_.value){const E=(k=j.value)===null||k===void 0?void 0:k.getPendingTmNode();E?Te(E):e.filterable||(ce(),He())}else if(he(),e.tag&&be.value){const E=O.value[0];if(E){const te=E[e.valueField],{value:me}=g;e.multiple&&Array.isArray(me)&&me.includes(te)||ue(E)}}}t.preventDefault();break;case"ArrowUp":if(t.preventDefault(),e.loading)return;_.value&&(($=j.value)===null||$===void 0||$.prev());break;case"ArrowDown":if(t.preventDefault(),e.loading)return;_.value?(M=j.value)===null||M===void 0||M.next():he();break;case"Escape":_.value&&(No(t),ce()),(B=L.value)===null||B===void 0||B.focus();break}}function He(){var t;(t=L.value)===null||t===void 0||t.focus()}function je(){var t;(t=L.value)===null||t===void 0||t.focusInput()}function ot(){var t;_.value&&((t=Y.value)===null||t===void 0||t.syncPosition())}G(),Oe(oe(e,"options"),G);const nt={focus:()=>{var t;(t=L.value)===null||t===void 0||t.focus()},focusInput:()=>{var t;(t=L.value)===null||t===void 0||t.focusInput()},blur:()=>{var t;(t=L.value)===null||t===void 0||t.blur()},blurInput:()=>{var t;(t=L.value)===null||t===void 0||t.blurInput()}},Ve=A(()=>{const{self:{menuBoxShadow:t}}=f.value;return{"--n-menu-box-shadow":t}}),ye=l?Ze("select",void 0,Ve,e):void 0;return Object.assign(Object.assign({},nt),{mergedStatus:K,mergedClsPrefix:o,mergedBordered:n,namespace:r,treeMate:R,isMounted:Eo(),triggerRef:L,menuRef:j,pattern:w,uncontrolledShow:I,mergedShow:_,adjustedTo:dt(e),uncontrolledValue:d,mergedValue:g,followerRef:Y,localizedPlaceholder:re,selectedOption:N,selectedOptions:x,mergedSize:X,mergedDisabled:H,focused:C,activeWithoutMenuOpen:be,inlineThemeDisabled:l,onTriggerInputFocus:Se,onTriggerInputBlur:pe,handleTriggerOrMenuResize:ot,handleMenuFocus:ke,handleMenuBlur:$e,handleMenuTabOut:Ee,handleTriggerClick:Be,handleToggle:Te,handleDeleteOption:ue,handlePatternInput:p,handleClear:Z,handleTriggerBlur:_e,handleTriggerFocus:Re,handleKeydown:De,handleMenuAfterLeave:we,handleMenuClickOutside:Ae,handleMenuScroll:tt,handleMenuKeydown:De,handleMenuMousedown:et,mergedTheme:f,cssVars:l?void 0:Ve,themeClass:ye?.themeClass,onRender:ye?.onRender})},render(){return u("div",{class:`${this.mergedClsPrefix}-select`},u(zo,null,{default:()=>[u(Mo,null,{default:()=>u(sn,{ref:"triggerRef",inlineThemeDisabled:this.inlineThemeDisabled,status:this.mergedStatus,inputProps:this.inputProps,clsPrefix:this.mergedClsPrefix,showArrow:this.showArrow,maxTagCount:this.maxTagCount,ellipsisTagPopoverProps:this.ellipsisTagPopoverProps,bordered:this.mergedBordered,active:this.activeWithoutMenuOpen||this.mergedShow,pattern:this.pattern,placeholder:this.localizedPlaceholder,selectedOption:this.selectedOption,selectedOptions:this.selectedOptions,multiple:this.multiple,renderTag:this.renderTag,renderLabel:this.renderLabel,filterable:this.filterable,clearable:this.clearable,disabled:this.mergedDisabled,size:this.mergedSize,theme:this.mergedTheme.peers.InternalSelection,labelField:this.labelField,valueField:this.valueField,themeOverrides:this.mergedTheme.peerOverrides.InternalSelection,loading:this.loading,focused:this.focused,onClick:this.handleTriggerClick,onDeleteOption:this.handleDeleteOption,onPatternInput:this.handlePatternInput,onClear:this.handleClear,onBlur:this.handleTriggerBlur,onFocus:this.handleTriggerFocus,onKeydown:this.handleKeydown,onPatternBlur:this.onTriggerInputBlur,onPatternFocus:this.onTriggerInputFocus,onResize:this.handleTriggerOrMenuResize,ignoreComposition:this.ignoreComposition},{arrow:()=>{var e,o;return[(o=(e=this.$slots).arrow)===null||o===void 0?void 0:o.call(e)]}})}),u(Bo,{ref:"followerRef",show:this.mergedShow,to:this.adjustedTo,teleportDisabled:this.adjustedTo===dt.tdkey,containerClass:this.namespace,width:this.consistentMenuWidth?"target":void 0,minWidth:"target",placement:this.placement},{default:()=>u(Ft,{name:"fade-in-scale-up-transition",appear:this.isMounted,onAfterLeave:this.handleMenuAfterLeave},{default:()=>{var e,o,n;return this.mergedShow||this.displayDirective==="show"?((e=this.onRender)===null||e===void 0||e.call(this),vo(u(Zo,Object.assign({},this.menuProps,{ref:"menuRef",onResize:this.handleTriggerOrMenuResize,inlineThemeDisabled:this.inlineThemeDisabled,virtualScroll:this.consistentMenuWidth&&this.virtualScroll,class:[`${this.mergedClsPrefix}-select-menu`,this.themeClass,(o=this.menuProps)===null||o===void 0?void 0:o.class],clsPrefix:this.mergedClsPrefix,focusable:!0,labelField:this.labelField,valueField:this.valueField,autoPending:!0,nodeProps:this.nodeProps,theme:this.mergedTheme.peers.InternalSelectMenu,themeOverrides:this.mergedTheme.peerOverrides.InternalSelectMenu,treeMate:this.treeMate,multiple:this.multiple,size:this.menuSize,renderOption:this.renderOption,renderLabel:this.renderLabel,value:this.mergedValue,style:[(n=this.menuProps)===null||n===void 0?void 0:n.style,this.cssVars],onToggle:this.handleToggle,onScroll:this.handleMenuScroll,onFocus:this.handleMenuFocus,onBlur:this.handleMenuBlur,onKeydown:this.handleMenuKeydown,onTabOut:this.handleMenuTabOut,onMousedown:this.handleMenuMousedown,show:this.mergedShow,showCheckmark:this.showCheckmark,resetMenuOnOptionsChange:this.resetMenuOnOptionsChange,scrollbarProps:this.scrollbarProps}),{empty:()=>{var r,l;return[(l=(r=this.$slots).empty)===null||l===void 0?void 0:l.call(r)]},header:()=>{var r,l;return[(l=(r=this.$slots).header)===null||l===void 0?void 0:l.call(r)]},action:()=>{var r,l;return[(l=(r=this.$slots).action)===null||l===void 0?void 0:l.call(r)]}}),this.displayDirective==="show"?[[go,this.mergedShow],[pt,this.handleMenuClickOutside,void 0,{capture:!0}]]:[[pt,this.handleMenuClickOutside,void 0,{capture:!0}]])):null}})})]}))}});export{Yo as F,mn as N,qo as V,at as a,Zo as b,cn as c,St as g,it as m};
